import asyncio
import json
import logging
import re

import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from . import get_samsung_host

_LOGGER = logging.getLogger(__name__)


def _slug_from_remote(entity_id: str) -> str:
    """Create a safe object ID from the Samsung remote entity."""

    object_id = entity_id.split(".", 1)[1]

    return re.sub(
        r"[^a-z0-9_]",
        "_",
        object_id.lower(),
    )


def _find_matching_media_player(
    hass: HomeAssistant,
    remote_entity_id: str,
) -> str | None:
    """Find the native Samsung media_player belonging to the same TV."""

    entity_registry = er.async_get(hass)

    remote_entry = entity_registry.async_get(
        remote_entity_id
    )

    if (
        remote_entry is None
        or remote_entry.config_entry_id is None
    ):
        return None

    for entry in entity_registry.entities.values():

        if (
            entry.domain == "media_player"
            and entry.platform == "samsungtv"
            and entry.config_entry_id
            == remote_entry.config_entry_id
        ):
            return entry.entity_id

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TV Mode sensors and dynamically discover Samsung TVs."""

    entity_registry = er.async_get(hass)

    # Track native Samsung remote entities for which this platform has
    # already created (or is currently creating) a TV Mode sensor.
    # This prevents duplicate entities when registry events arrive in
    # quick succession while a Samsung config entry is being created.
    known_remotes: set[str] = set()
    pending_remotes: set[str] = set()

    async def _async_add_remote(
        remote_entity_id: str,
        *,
        wait_for_media_player: bool = False,
    ) -> None:
        """Add a TV Mode sensor for one native Samsung remote."""

        if (
            remote_entity_id in known_remotes
            or remote_entity_id in pending_remotes
        ):
            return

        remote_entry = entity_registry.async_get(remote_entity_id)

        if (
            remote_entry is None
            or remote_entry.domain != "remote"
            or remote_entry.platform != "samsungtv"
        ):
            return

        pending_remotes.add(remote_entity_id)

        try:
            # During runtime setup Home Assistant may register the Samsung
            # remote a moment before the matching media_player entity. Give
            # the native integration a short window to finish registering
            # the TV so our sensor is created with both source entities.
            media_player_entity_id = _find_matching_media_player(
                hass,
                remote_entity_id,
            )

            if wait_for_media_player and media_player_entity_id is None:
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    media_player_entity_id = _find_matching_media_player(
                        hass,
                        remote_entity_id,
                    )
                    if media_player_entity_id is not None:
                        break

            try:
                host = get_samsung_host(
                    hass,
                    remote_entity_id,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Could not resolve Samsung TV for %s: %s",
                    remote_entity_id,
                    err,
                )
                return

            async_add_entities(
                [
                    SamsungTVModeSensor(
                        hass,
                        remote_entity_id,
                        media_player_entity_id,
                        host,
                    )
                ],
                True,
            )

            known_remotes.add(remote_entity_id)

            _LOGGER.info(
                "Dynamically added Samsung Smart Remote TV Mode sensor for %s",
                remote_entity_id,
            )

        finally:
            pending_remotes.discard(remote_entity_id)

    # Add every Samsung TV that already exists when this integration loads.
    samsung_remotes = [
        entry.entity_id
        for entry in entity_registry.entities.values()
        if (
            entry.domain == "remote"
            and entry.platform == "samsungtv"
        )
    ]

    if not samsung_remotes:
        _LOGGER.warning(
            "Samsung Smart Remote found no native Samsung TV remote entities. "
            "New Samsung TVs will still be discovered automatically."
        )

    for remote_entity_id in samsung_remotes:
        await _async_add_remote(remote_entity_id)

    async def _async_entity_registry_updated(event) -> None:
        """Watch for Samsung remote entities added after startup."""

        action = event.data.get("action")

        if action not in ("create", "update"):
            return

        entity_id = event.data.get("entity_id")

        if not isinstance(entity_id, str):
            return

        # Entity renames can arrive as an update with old_entity_id. The
        # registry lookup below is authoritative, so only remote.* candidates
        # need further inspection.
        if not entity_id.startswith("remote."):
            return

        await _async_add_remote(
            entity_id,
            wait_for_media_player=True,
        )

    entry.async_on_unload(
        hass.bus.async_listen(
            EVENT_ENTITY_REGISTRY_UPDATED,
            _async_entity_registry_updated,
        )
    )


class SamsungTVModeSensor(SensorEntity):
    """Represent the operating mode of a Samsung television."""

    _attr_should_poll = True
    _attr_icon = "mdi:television"

    def __init__(
        self,
        hass: HomeAssistant,
        remote_entity_id: str,
        media_player_entity_id: str | None,
        host: str,
    ) -> None:
        """Initialize the Samsung TV Mode sensor."""

        self.hass = hass

        self._remote_entity_id = (
            remote_entity_id
        )

        self._media_player_entity_id = (
            media_player_entity_id
        )

        self._host = host

        slug = _slug_from_remote(
            remote_entity_id
        )

        self.entity_id = (
            f"sensor.{slug}_tv_mode"
        )

        self._attr_unique_id = (
            f"samsung_smart_remote_{slug}_tv_mode"
        )

        self._attr_name = (
            f"{slug.replace('_', ' ').title()} TV Mode"
        )

        self._attr_native_value = "unknown"

        #
        # None  = not detected yet
        # True  = Frame / Art Mode capable
        # False = ordinary Samsung TV
        #

        self._art_mode_supported = None


    async def async_added_to_hass(self) -> None:
        """Refresh immediately when the native Samsung state changes."""

        await super().async_added_to_hass()

        tracked_entities = [self._remote_entity_id]

        if self._media_player_entity_id:
            tracked_entities.append(self._media_player_entity_id)

        async def _async_native_state_changed(event) -> None:
            """Synchronize this sensor with the native Samsung entities."""

            await self.async_update_ha_state(True)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                tracked_entities,
                _async_native_state_changed,
            )
        )


    @property
    def extra_state_attributes(self) -> dict:
        """Return TV state/capability information."""

        return {
            "source_entity":
                self._remote_entity_id,

            "media_player_entity":
                self._media_player_entity_id,

            "host":
                self._host,

            "art_mode_supported":
                self._art_mode_supported,
        }


    async def async_update(self) -> None:
        """Determine the current Samsung TV operating mode."""

        remote_state = self.hass.states.get(
            self._remote_entity_id
        )

        media_state = None

        if self._media_player_entity_id:
            media_state = self.hass.states.get(
                self._media_player_entity_id
            )


        remote_value = (
            remote_state.state
            if remote_state
            else None
        )

        media_value = (
            media_state.state
            if media_state
            else None
        )


        #
        # -------------------------------------------------
        # 1. EXPLICIT OFF
        # -------------------------------------------------
        #
        # Either native Samsung entity explicitly reporting
        # OFF is strong evidence that the television itself
        # is powered down.
        #

        if (
            remote_value == "off"
            or media_value == "off"
        ):
            self._attr_native_value = "off"
            return


        #
        # -------------------------------------------------
        # 2. TV IS REACHABLE / ON
        # -------------------------------------------------
        #
        # Art Mode keeps the native Samsung entities ON,
        # which is exactly why we need the Art WebSocket
        # distinction.
        #

        native_on = (
            remote_value == "on"
            or media_value == "on"
        )

        if native_on:

            #
            # Detect Art capability once the television
            # is reachable.
            #

            if self._art_mode_supported is None:
                await self._async_detect_capabilities()


            #
            # Ordinary Samsung TV.
            #

            if self._art_mode_supported is False:
                self._attr_native_value = "on"
                return


            #
            # Samsung Frame / Art-capable television.
            #

            if self._art_mode_supported is True:

                art_state = (
                    await self._async_get_art_mode()
                )

                if art_state == "on":
                    self._attr_native_value = "art"
                    return

                if art_state == "off":
                    self._attr_native_value = "on"
                    return

                #
                # Native HA says the television is ON,
                # but Art Mode cannot presently be
                # determined.
                #
                # Preserve UNKNOWN rather than falsely
                # claiming viewing or Art Mode.
                #

                self._attr_native_value = "unknown"
                return


            self._attr_native_value = "unknown"
            return


        #
        # -------------------------------------------------
        # 3. BOTH NATIVE ENTITIES LOST
        # -------------------------------------------------
        #
        # On a completely shut-down Samsung, the native
        # entities may initially report OFF and then become
        # unavailable/unknown after the television stops
        # answering.
        #
        # If our own last trustworthy state was OFF, keep
        # OFF rather than changing it back to UNKNOWN.
        #
        # This is the important fix for the behavior we
        # just observed.
        #

        remote_lost = (
            remote_value
            in (
                None,
                "unknown",
                "unavailable",
            )
        )

        media_lost = (
            media_value
            in (
                None,
                "unknown",
                "unavailable",
            )
        )


        if (
            remote_lost
            and media_lost
            and self._attr_native_value == "off"
        ):
            return


        #
        # We have no trustworthy evidence that the TV
        # is ON, in Art Mode, or deliberately OFF.
        #
        # This remains UNKNOWN so an actual network or
        # integration failure isn't misrepresented as OFF.
        #

        self._attr_native_value = "unknown"


    async def _async_detect_capabilities(
        self,
    ) -> None:
        """Read Samsung device capabilities from /api/v2/."""

        session = async_get_clientsession(
            self.hass
        )

        url = (
            f"http://{self._host}:8001/api/v2/"
        )

        try:
            async with asyncio.timeout(5):

                async with session.get(
                    url
                ) as response:

                    response.raise_for_status()

                    data = await response.json()

            device = data.get(
                "device",
                {},
            )

            frame_support = device.get(
                "FrameTVSupport"
            )


            if isinstance(
                frame_support,
                bool,
            ):

                self._art_mode_supported = (
                    frame_support
                )

            elif isinstance(
                frame_support,
                str,
            ):

                self._art_mode_supported = (
                    frame_support.strip().lower()
                    == "true"
                )

            elif frame_support is None:

                self._art_mode_supported = False

            else:

                self._art_mode_supported = bool(
                    frame_support
                )


            _LOGGER.info(
                "Samsung TV %s at %s: "
                "Art Mode support = %s",
                self._remote_entity_id,
                self._host,
                self._art_mode_supported,
            )


        except Exception as err:

            #
            # A temporary REST failure must not cause us
            # to permanently classify a Frame as a
            # non-Frame.
            #

            self._art_mode_supported = None

            _LOGGER.warning(
                "Could not determine Samsung TV "
                "capabilities for %s (%s): %s",
                self._remote_entity_id,
                self._host,
                err,
            )


    async def _async_get_art_mode(
        self,
    ) -> str | None:
        """Query Samsung Art WebSocket for current Art Mode."""

        session = async_get_clientsession(
            self.hass
        )

        #
        # "Home Assistant" encoded in base64.
        #

        name = "SG9tZSBBc3Npc3RhbnQ="

        url = (
            f"wss://{self._host}:8002/"
            "api/v2/channels/"
            "com.samsung.art-app"
            f"?name={name}"
        )

        ws = None

        try:

            _LOGGER.warning(
                "ART-DIAG %s (%s): opening Art WebSocket %s",
                self._remote_entity_id, self._host, url,
            )

            ws = await session.ws_connect(
                url,
                ssl=False,
                timeout=7,
            )


            #
            # Wait for Samsung's Art channel.
            #

            ready = False

            try:
                async with asyncio.timeout(7):

                    while True:

                        message = await ws.receive()

                        if message.type.name == "TEXT":

                            try:
                                response = (
                                    message.json()
                                )

                            except Exception:
                                continue


                            if (
                                response.get("event")
                                == "ms.channel.ready"
                            ):
                                ready = True
                                break


                        elif message.type.name in (
                            "CLOSE",
                            "CLOSED",
                            "ERROR",
                        ):
                            break


            except TimeoutError:
                pass


            if not ready:
                _LOGGER.warning(
                    "ART-DIAG %s (%s): Art channel never became ready",
                    self._remote_entity_id, self._host,
                )
                return None


            #
            # Request current Art Mode state.
            #

            command = {
                "method": "ms.channel.emit",

                "params": {
                    "event": "art_app_request",
                    "to": "host",

                    "data": json.dumps(
                        {
                            "request":
                                "get_artmode_status",

                            "id":
                                "ha_artmode_status",
                        }
                    ),
                },
            }


            await ws.send_json(
                command
            )

            _LOGGER.warning(
                "ART-DIAG %s (%s): sent get_artmode_status request=%s",
                self._remote_entity_id, self._host, command,
            )


            #
            # Wait for the Samsung Art response.
            #

            try:
                async with asyncio.timeout(7):

                    while True:

                        message = await ws.receive()


                        if message.type.name == "TEXT":

                            _LOGGER.warning(
                                "ART-DIAG %s (%s): response raw=%s",
                                self._remote_entity_id, self._host, message.data,
                            )

                            try:
                                response = (
                                    message.json()
                                )

                            except Exception:
                                continue


                            if (
                                response.get("event")
                                != "d2d_service_message"
                            ):
                                continue


                            data = response.get(
                                "data"
                            )

                            if not data:
                                continue


                            if isinstance(
                                data,
                                str,
                            ):

                                try:
                                    data = json.loads(
                                        data
                                    )

                                except json.JSONDecodeError:
                                    continue


                            if not isinstance(
                                data,
                                dict,
                            ):
                                continue


                            event = data.get(
                                "event"
                            )


                            #
                            # Direct response to
                            # get_artmode_status.
                            #

                            if event == "artmode_status":

                                value = data.get(
                                    "value"
                                )

                                if value == "on":
                                    return "on"

                                if value in (
                                    "off",
                                    "nav",
                                ):
                                    return "off"


                            #
                            # Some Samsung firmware sends
                            # this transition event.
                            #

                            if event == "art_mode_changed":

                                status = data.get(
                                    "status"
                                )

                                if status == "on":
                                    return "on"

                                if status == "off":
                                    return "off"


                            if event == "go_to_standby":
                                return "off"


                        elif message.type.name in (
                            "CLOSE",
                            "CLOSED",
                            "ERROR",
                        ):
                            break


            except TimeoutError:
                pass


        except (
            aiohttp.ClientError,
            TimeoutError,
            OSError,
        ) as err:

            _LOGGER.warning(
                "ART-DIAG Samsung Art Mode query failed 
                "for %s (%s): %s",
                self._remote_entity_id,
                self._host,
                err,
            )


        except Exception as err:

            _LOGGER.warning(
                "Unexpected Samsung Art Mode error "
                "for %s (%s): %s",
                self._remote_entity_id,
                self._host,
                err,
            )


        finally:

            if ws is not None:

                try:
                    await ws.close()

                except Exception:
                    pass


        return None
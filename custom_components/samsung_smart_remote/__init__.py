import asyncio
import base64
import json
import logging
import socket
import ipaddress

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession


DOMAIN = "samsung_smart_remote"
PLATFORMS = [Platform.SENSOR]
CONF_WOL_BROADCAST = "wol_broadcast_address"

SERVICE_LAUNCH_APP = "launch_app"
SERVICE_SET_MODE = "set_mode"

ATTR_ENTITY_ID = "entity_id"
ATTR_APP_ID = "app_id"
ATTR_MODE = "mode"

MODE_ON = "on"
MODE_ART = "art"
MODE_OFF = "off"

_LOGGER = logging.getLogger(__name__)


LAUNCH_APP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_APP_ID): cv.string,
    }
)


SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_MODE): vol.In(
            [
                MODE_ON,
                MODE_ART,
                MODE_OFF,
            ]
        ),
    }
)


# =========================================================
# SAMSUNG DEVICE INFORMATION
# =========================================================


def get_samsung_entity_entry(
    hass: HomeAssistant,
    entity_id: str,
):
    """Return the native Samsung entity registry entry."""

    entity_registry = er.async_get(hass)

    entity_entry = entity_registry.async_get(
        entity_id
    )

    if entity_entry is None:
        raise ValueError(
            f"Entity {entity_id} was not found "
            "in the Home Assistant entity registry."
        )

    return entity_entry


def get_samsung_config_entry(
    hass: HomeAssistant,
    entity_id: str,
):
    """Return the native Samsung config entry."""

    entity_entry = get_samsung_entity_entry(
        hass,
        entity_id,
    )

    if entity_entry.config_entry_id is None:
        raise ValueError(
            f"Entity {entity_id} does not have "
            "a config entry."
        )

    config_entry = hass.config_entries.async_get_entry(
        entity_entry.config_entry_id
    )

    if config_entry is None:
        raise ValueError(
            f"Could not find the config entry "
            f"for {entity_id}."
        )

    return config_entry


def get_samsung_host(
    hass: HomeAssistant,
    entity_id: str,
) -> str:
    """Resolve Samsung TV host."""

    config_entry = get_samsung_config_entry(
        hass,
        entity_id,
    )

    host = config_entry.data.get(
        CONF_HOST
    )

    if not host:
        raise ValueError(
            f"Could not determine Samsung TV "
            f"host for {entity_id}."
        )

    return host


def get_samsung_token(
    hass: HomeAssistant,
    entity_id: str,
) -> str | None:
    """Get Samsung WebSocket authorization token."""

    config_entry = get_samsung_config_entry(
        hass,
        entity_id,
    )

    token = config_entry.data.get(
        CONF_TOKEN
    )

    if token is None:
        return None

    return str(token)


def get_samsung_mac(
    hass: HomeAssistant,
    entity_id: str,
) -> str | None:
    """
    Get the TV MAC address.

    Prefer the MAC stored by Home Assistant's native
    Samsung integration. Fall back to the HA device registry.
    """

    config_entry = get_samsung_config_entry(
        hass,
        entity_id,
    )

    mac = config_entry.data.get(
        CONF_MAC
    )

    if mac:
        return str(mac)

    entity_entry = get_samsung_entity_entry(
        hass,
        entity_id,
    )

    if entity_entry.device_id is None:
        return None

    device_registry = dr.async_get(
        hass
    )

    device_entry = device_registry.async_get(
        entity_entry.device_id
    )

    if device_entry is None:
        return None

    for connection_type, connection_value in (
        device_entry.connections
    ):

        if (
            connection_type
            == dr.CONNECTION_NETWORK_MAC
        ):
            return str(connection_value)

    return None


def get_tv_mode_entity(
    hass: HomeAssistant,
    remote_entity_id: str,
) -> str | None:
    """Find our TV Mode sensor for this Samsung remote."""

    for state in hass.states.async_all(
        "sensor"
    ):

        if (
            state.attributes.get("source_entity")
            == remote_entity_id
            and state.entity_id.endswith(
                "_tv_mode"
            )
        ):
            return state.entity_id

    return None


# =========================================================
# WAKE-ON-LAN
# =========================================================


async def async_send_wol(
    hass: HomeAssistant,
    mac: str,
    host: str,
) -> None:
    """Wake Samsung TV with a directed-broadcast magic packet.

    v0.1.3 test implementation: derive the directed broadcast from the
    Samsung TV IPv4 address using the /24 LAN layout proven at 19CEL.
    This intentionally avoids Home Assistant's wake_on_lan service so
    the custom integration can be tested without `wake_on_lan:` YAML.
    """

    # Prefer an administrator-configured directed broadcast address.
    # If none is configured, retain the v0.1.3 proven /24 derivation.
    broadcast_address = None
    entries = hass.config_entries.async_entries(DOMAIN)
    if entries:
        configured = entries[0].options.get(CONF_WOL_BROADCAST)
        if configured:
            try:
                broadcast_address = str(ipaddress.IPv4Address(configured))
            except ipaddress.AddressValueError as err:
                raise RuntimeError(
                    f"Invalid configured Wake-on-LAN broadcast address: {configured}"
                ) from err

    if broadcast_address is None:
        try:
            host_ip = ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError as err:
            raise RuntimeError(
                f"Cannot derive Wake-on-LAN broadcast address from host {host}."
            ) from err

        broadcast_address = str(
            ipaddress.IPv4Network(f"{host_ip}/24", strict=False).broadcast_address
        )

    clean_mac = mac.replace(":", "").replace("-", "").strip()
    if len(clean_mac) != 12:
        raise RuntimeError(f"Invalid MAC address for Wake-on-LAN: {mac}")

    try:
        mac_bytes = bytes.fromhex(clean_mac)
    except ValueError as err:
        raise RuntimeError(
            f"Invalid MAC address for Wake-on-LAN: {mac}"
        ) from err

    packet = b"\xff" * 6 + mac_bytes * 16

    def _send_magic_packet() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (broadcast_address, 9))

    _LOGGER.warning(
        "Samsung Smart Remote: sending directed WOL to %s via %s:9",
        mac,
        broadcast_address,
    )

    await hass.async_add_executor_job(_send_magic_packet)


# =========================================================
# SAMSUNG REMOTE WEBSOCKET
# =========================================================


def build_remote_ws_url(
    host: str,
    token: str | None,
) -> str:
    """Build authenticated Samsung remote WebSocket URL."""

    name = base64.b64encode(
        b"Home Assistant"
    ).decode()

    url = (
        f"wss://{host}:8002/"
        "api/v2/channels/"
        "samsung.remote.control"
        f"?name={name}"
    )

    if token:
        url += f"&token={token}"

    return url


async def async_wait_for_remote_connection(
    ws,
) -> None:
    """Wait for Samsung remote authorization."""

    try:

        async with asyncio.timeout(
            7
        ):

            while True:

                message = await ws.receive()

                if message.type.name == "TEXT":

                    try:
                        data = message.json()

                    except Exception:
                        continue

                    event = data.get(
                        "event"
                    )

                    if event == "ms.channel.connect":
                        return

                    if event == "ms.channel.unauthorized":
                        raise RuntimeError(
                            "Samsung TV rejected the "
                            "WebSocket authorization token."
                        )

                elif message.type.name in (
                    "CLOSE",
                    "CLOSED",
                    "ERROR",
                ):
                    raise RuntimeError(
                        "Samsung TV closed the remote "
                        "WebSocket before authorization."
                    )

    except TimeoutError as err:

        raise RuntimeError(
            "Timed out waiting for Samsung remote "
            "WebSocket authorization."
        ) from err


async def async_send_remote_key(
    hass: HomeAssistant,
    host: str,
    token: str | None,
    key: str,
) -> None:
    """
    Send one Samsung remote key using the authenticated
    Samsung remote-control WebSocket.
    """

    if not token:
        raise RuntimeError(
            f"No Samsung WebSocket authorization token "
            f"is available for TV at {host}."
        )

    session = async_get_clientsession(
        hass
    )

    url = build_remote_ws_url(
        host,
        token,
    )

    ws = None

    try:

        ws = await session.ws_connect(
            url,
            ssl=False,
            timeout=10,
        )

        await async_wait_for_remote_connection(
            ws
        )

        payload = {
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Click",
                "DataOfCmd": key,
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        }

        _LOGGER.warning(
            "Samsung Smart Remote: sending %s to %s",
            key,
            host,
        )

        await ws.send_json(
            payload
        )

        await asyncio.sleep(
            0.5
        )

    finally:

        if ws is not None:

            try:
                await ws.close()

            except Exception:
                pass


# =========================================================
# ART MODE
# =========================================================


async def async_set_art_mode(
    hass: HomeAssistant,
    host: str,
    enabled: bool,
) -> None:
    """Explicitly enable or disable Samsung Art Mode."""

    session = async_get_clientsession(
        hass
    )

    name = base64.b64encode(
        b"Home Assistant"
    ).decode()

    url = (
        f"wss://{host}:8002/"
        "api/v2/channels/"
        "com.samsung.art-app"
        f"?name={name}"
    )

    ws = None

    try:

        ws = await session.ws_connect(
            url,
            ssl=False,
            timeout=7,
        )

        ready = False

        try:

            async with asyncio.timeout(
                7
            ):

                while True:

                    message = await ws.receive()

                    if message.type.name == "TEXT":

                        try:
                            response = message.json()

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
            raise RuntimeError(
                "Samsung Art Mode channel "
                "did not become ready."
            )

        request = {
            "request": "set_artmode_status",
            "value": (
                "on"
                if enabled
                else "off"
            ),
            "id": "ha_set_artmode_status",
        }

        payload = {
            "method": "ms.channel.emit",
            "params": {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(
                    request
                ),
            },
        }

        await ws.send_json(
            payload
        )

        await asyncio.sleep(
            0.5
        )

    finally:

        if ws is not None:

            try:
                await ws.close()

            except Exception:
                pass


# =========================================================
# WAIT FOR TV AFTER WOL
# =========================================================


async def async_wait_for_tv_ready(
    hass: HomeAssistant,
    host: str,
    timeout_seconds: int = 25,
) -> None:
    """
    Wait for Samsung's network stack to become reachable
    after Wake-on-LAN.
    """

    session = async_get_clientsession(
        hass
    )

    deadline = (
        asyncio.get_running_loop().time()
        + timeout_seconds
    )

    url = (
        f"https://{host}:8002/api/v2/"
    )

    while (
        asyncio.get_running_loop().time()
        < deadline
    ):

        try:

            async with session.get(
                url,
                ssl=False,
                timeout=3,
            ) as response:

                if response.status < 500:

                    _LOGGER.warning(
                        "Samsung Smart Remote: TV %s "
                        "network API is ready",
                        host,
                    )

                    return

        except Exception:
            pass

        await asyncio.sleep(
            1
        )

    raise RuntimeError(
        f"Samsung TV at {host} did not become "
        f"reachable within {timeout_seconds} seconds."
    )


# =========================================================
# HARD POWER OFF
# =========================================================


async def async_hard_power_off(
    hass: HomeAssistant,
    host: str,
    token: str,
) -> None:
    """
    Fully power off Samsung TV.

    Verified sequence:

        KEY_POWER Press
        wait 4 seconds
        KEY_POWER Release
    """

    session = async_get_clientsession(
        hass
    )

    url = build_remote_ws_url(
        host,
        token,
    )

    ws = None

    try:

        ws = await session.ws_connect(
            url,
            ssl=False,
            timeout=10,
        )

        await async_wait_for_remote_connection(
            ws
        )

        press_payload = {
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Press",
                "DataOfCmd": "KEY_POWER",
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        }

        release_payload = {
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Release",
                "DataOfCmd": "KEY_POWER",
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        }

        _LOGGER.warning(
            "Samsung Smart Remote: KEY_POWER Press to %s",
            host,
        )

        await ws.send_json(
            press_payload
        )

        await asyncio.sleep(
            4.0
        )

        _LOGGER.warning(
            "Samsung Smart Remote: KEY_POWER Release to %s",
            host,
        )

        try:

            await ws.send_json(
                release_payload
            )

            await asyncio.sleep(
                0.25
            )

        except Exception as err:

            _LOGGER.debug(
                "Samsung TV %s closed WebSocket "
                "during Power release: %s",
                host,
                err,
            )

    finally:

        if ws is not None:

            try:
                await ws.close()

            except Exception:
                pass


# =========================================================
# SETUP
# =========================================================


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Samsung Smart Remote from a config entry."""

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # =====================================================
    # LAUNCH APP
    # =====================================================

    async def async_launch_app(
        call: ServiceCall,
    ) -> None:

        entity_id = call.data[
            ATTR_ENTITY_ID
        ]

        app_id = call.data[
            ATTR_APP_ID
        ]

        host = get_samsung_host(
            hass,
            entity_id,
        )

        url = (
            f"wss://{host}:8002/api/v2"
        )

        command = {
            "id": app_id,
            "method": "ms.application.start",
            "params": {
                "id": app_id,
            },
        }

        session = async_get_clientsession(
            hass
        )

        ws = None

        try:

            ws = await session.ws_connect(
                url,
                ssl=False,
                timeout=10,
            )

            try:

                async with asyncio.timeout(
                    5
                ):
                    await ws.receive()

            except TimeoutError:
                pass

            await ws.send_json(
                command
            )

            await asyncio.sleep(
                0.5
            )

        finally:

            if ws is not None:

                try:
                    await ws.close()

                except Exception:
                    pass


    # =====================================================
    # SET MODE
    # =====================================================

    async def async_set_mode(
        call: ServiceCall,
    ) -> None:

        entity_id = call.data[
            ATTR_ENTITY_ID
        ]

        requested_mode = call.data[
            ATTR_MODE
        ]

        host = get_samsung_host(
            hass,
            entity_id,
        )

        token = get_samsung_token(
            hass,
            entity_id,
        )

        mac = get_samsung_mac(
            hass,
            entity_id,
        )

        mode_entity_id = get_tv_mode_entity(
            hass,
            entity_id,
        )

        current_mode = None
        art_supported = False
        media_player_entity_id = None

        if mode_entity_id:

            mode_state = hass.states.get(
                mode_entity_id
            )

            if mode_state:

                current_mode = (
                    mode_state.state
                )

                art_supported = bool(
                    mode_state.attributes.get(
                        "art_mode_supported",
                        False,
                    )
                )

                media_player_entity_id = (
                    mode_state.attributes.get(
                        "media_player_entity"
                    )
                )

        _LOGGER.warning(
            "Samsung TV %s mode request: "
            "%s -> %s, host=%s, mac=%s, art=%s",
            entity_id,
            current_mode,
            requested_mode,
            host,
            mac,
            art_supported,
        )

        if current_mode == requested_mode:
            return

        # =================================================
        # OFF
        # =================================================

        if requested_mode == MODE_OFF:

            # Frame / Art-capable TVs keep the proven long-press
            # hard-off behavior. Standard Samsung TVs use the
            # single KEY_POWER click used by HA's native Samsung
            # integration and verified on the 2025 S85F OLED.

            if art_supported:

                if not token:
                    raise RuntimeError(
                        f"No Samsung WebSocket authorization "
                        f"token is stored for {entity_id}. "
                        "Cannot safely perform long-press OFF."
                    )

                await async_hard_power_off(
                    hass,
                    host,
                    token,
                )

            else:

                await async_send_remote_key(
                    hass,
                    host,
                    token,
                    "KEY_POWER",
                )

            return

        # =================================================
        # ART
        # =================================================

        if requested_mode == MODE_ART:

            if not art_supported:
                raise ValueError(
                    f"{entity_id} does not advertise "
                    "Art Mode support."
                )

            # OFF -> ART: a Frame naturally wakes into Art Mode.
            if current_mode == MODE_OFF:

                if not mac:
                    raise RuntimeError(
                        f"No MAC address is stored for "
                        f"{entity_id}; cannot wake TV."
                    )

                await async_send_wol(
                    hass,
                    mac,
                    host,
                )

                return

            # Normal viewing -> ART.
            await async_set_art_mode(
                hass,
                host,
                True,
            )
            return

        # =================================================
        # ON / NORMAL VIEWING
        # =================================================

        if requested_mode == MODE_ON:

            # ART -> ON: preserve the proven Frame Art API path.
            if current_mode == MODE_ART:

                await async_set_art_mode(
                    hass,
                    host,
                    False,
                )

                return

            if current_mode == MODE_OFF:

                # Standard Samsung: delegate wake-up to the native
                # Samsung media_player. This is verified on the
                # 2025 QN55S85FAFXZA and keeps this test change
                # isolated from the Frame wake path.
                if not art_supported:

                    if not mac:
                        raise RuntimeError(
                            f"No MAC address is stored for "
                            f"{entity_id}; cannot wake TV."
                        )

                    await async_send_wol(
                        hass,
                        mac,
                        host,
                    )

                    return

                # Frame OFF -> ON: retain the proven wake + HOME
                # sequence for normal viewing.
                if not mac:
                    raise RuntimeError(
                        f"No MAC address is stored for "
                        f"{entity_id}; cannot wake TV."
                    )

                if not token:
                    raise RuntimeError(
                        f"No Samsung WebSocket authorization "
                        f"token is stored for {entity_id}; "
                        "cannot send KEY_HOME after wake."
                    )

                await async_send_wol(
                    hass,
                    mac,
                    host,
                )

                await async_wait_for_tv_ready(
                    hass,
                    host,
                )

                await asyncio.sleep(
                    2
                )

                await async_send_remote_key(
                    hass,
                    host,
                    token,
                    "KEY_HOME",
                )

                return

            if current_mode in (
                None,
                "unknown",
                "unavailable",
            ):
                raise RuntimeError(
                    "Samsung TV state is unknown; "
                    "refusing to toggle Power."
                )


    # =====================================================
    # REGISTER SERVICES
    # =====================================================

    if not hass.services.has_service(
        DOMAIN,
        SERVICE_LAUNCH_APP,
    ):

        hass.services.async_register(
            DOMAIN,
            SERVICE_LAUNCH_APP,
            async_launch_app,
            schema=LAUNCH_APP_SCHEMA,
        )

    if not hass.services.has_service(
        DOMAIN,
        SERVICE_SET_MODE,
    ):

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_MODE,
            async_set_mode,
            schema=SET_MODE_SCHEMA,
        )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload Samsung Smart Remote config entry."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    # This integration allows only one config entry, so services can be
    # removed safely when that entry is unloaded.
    hass.services.async_remove(DOMAIN, SERVICE_LAUNCH_APP)
    hass.services.async_remove(DOMAIN, SERVICE_SET_MODE)
    return True
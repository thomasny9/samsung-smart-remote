# Samsung Smart Remote — Home Assistant Integration

Companion integration for the Samsung Smart Remote Lovelace card. It uses Home Assistant's native Samsung TV integration as the source of TV identity, host, token and MAC information.

**Current stable version: v0.2.0**

The v0.2.0 backend has been validated both as an upgrade on an existing Home Assistant installation and as a clean HACS installation on a second Home Assistant installation with multiple Samsung televisions.

## Features

- Local Samsung TV control through Home Assistant
- TV mode sensors (`on`, `off`, `art`)
- Samsung Frame Art Mode support
- Directed-broadcast Wake-on-LAN with an optional configured broadcast address
- Dynamic discovery of Samsung TVs added after this integration is loaded
- Local Samsung app launching service
- No `wake_on_lan:` YAML requirement
- Modern Home Assistant config-entry platform setup

## Requirements

Add each television with Home Assistant's native **Samsung Smart TV** integration first.

## Installation with HACS

1. In HACS, add this repository as a custom **Integration** repository.
2. Install **Samsung Smart Remote**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add Integration → Samsung Smart Remote**.
5. The integration automatically creates TV Mode sensors for compatible Samsung TVs exposed by Home Assistant's native Samsung integration.

Use **Configure** on the integration to set an optional Wake-on-LAN broadcast address. On a normal `/24` network, for example a TV at `10.0.12.195`, the directed broadcast is `10.0.12.255`. If left blank, the integration currently derives a `/24` broadcast from the TV address.

The companion dashboard card is installed separately through its HACS dashboard/plugin repository.

## Services

### `samsung_smart_remote.launch_app`

Launches a Samsung application by app ID.

### `samsung_smart_remote.set_mode`

Sets the requested TV mode: `on`, `off`, or `art` where supported.

## Tested configurations

The integration has been tested with Samsung Frame, Q-series and OLED televisions spanning multiple model years. Testing includes normal power control, directed Wake-on-LAN, Frame Art Mode, app launching, TV Mode sensor synchronization and dynamic discovery.

A clean HACS installation was also validated on a separate Home Assistant installation with multiple Samsung TVs, including Frame and Q-series models.

## Compatibility note

Samsung network-remote key support varies by television generation. An older Q7FN tested successfully for TV control but did not relay the network navigation commands to an attached Apple TV over HDMI-CEC. Newer tested Samsung models did.

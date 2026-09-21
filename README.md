# Samsung Smart Remote — Home Assistant Integration

Companion integration for the Samsung Smart Remote Lovelace card. It uses Home Assistant's native Samsung TV integration as the source of TV identity, host, token and MAC information.

## Features

- Local Samsung TV control through Home Assistant
- TV mode sensors (`on`, `off`, `art`)
- Samsung Frame Art Mode support
- Directed-broadcast Wake-on-LAN with an optional configured broadcast address
- Dynamic discovery of Samsung TVs added after this integration is loaded
- Local Samsung app launching service
- No `wake_on_lan:` YAML requirement

## Requirements

Add each television with Home Assistant's native **Samsung Smart TV** integration first.

## Installation with HACS

Add this repository to HACS as a custom **Integration** repository, install **Samsung Smart Remote**, and restart Home Assistant. Then go to **Settings → Devices & services → Add Integration → Samsung Smart Remote**.

Use **Configure** on the integration to set an optional Wake-on-LAN broadcast address. On a normal `/24` network, for example a TV at `10.0.12.195`, the directed broadcast is `10.0.12.255`. If left blank, the integration currently derives a `/24` broadcast from the TV address.

The companion dashboard card is installed separately through its HACS dashboard/plugin repository.

## Services

### `samsung_smart_remote.launch_app`
Launches a Samsung application by app ID.

### `samsung_smart_remote.set_mode`
Sets the requested TV mode: `on`, `off`, or `art` where supported.

## Compatibility note

Samsung network-remote key support varies by television generation. An older Q7FN tested successfully for TV control but did not relay the network navigation commands to an attached Apple TV over HDMI-CEC. Newer tested Samsung models did.

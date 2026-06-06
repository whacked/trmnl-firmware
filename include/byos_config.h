#ifndef BYOS_CONFIG_H
#define BYOS_CONFIG_H

// ============================================================================
//  BYOS (Bring Your Own Server) configuration
// ----------------------------------------------------------------------------
//  This is only the COMPILE-TIME DEFAULT server URL. It is intentionally NOT a
//  hardcoded LAN IP. There are two ways to point the device at your server:
//
//  1. Runtime (preferred, no reflash): enter your server URL in the "server"
//     field on the device's WiFi setup portal. It is saved to NVS ("api_url")
//     and overrides this default. (See lib/wificaptive — saveApiServer().)
//
//  2. Build time: override this default with a build flag, e.g.
//       PLATFORMIO_BUILD_FLAGS='-D BYOS_SERVER_URL=\"http://192.168.1.107:8080\"'
//     or add it to the env's build_flags in platformio.ini.
//
//  If neither is set, the device falls back to the stock TRMNL cloud below
//  (which keeps the original "register your MAC" behavior). Use http:// for a
//  plain LAN server — the firmware picks WiFiClient vs WiFiClientSecure from the
//  URL scheme (lib/trmnl/include/http_client.h). A ready-to-run reference server
//  lives in byos/ (run it with: ./byos/server.py).
// ============================================================================

#ifndef BYOS_SERVER_URL
#define BYOS_SERVER_URL "https://trmnl.app"
#endif

#endif // BYOS_CONFIG_H

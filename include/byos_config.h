#ifndef BYOS_CONFIG_H
#define BYOS_CONFIG_H

// ============================================================================
//  BYOS (Bring Your Own Server) configuration
// ----------------------------------------------------------------------------
//  Point the device at YOUR server instead of https://trmnl.app.
//
//  >>> CHANGE THIS ONE LINE, then rebuild + reflash. <<<
//
//  - Use http:// for a plain LAN server (no TLS / certificates needed).
//    The firmware picks WiFiClient (http) vs WiFiClientSecure (https)
//    automatically from the URL scheme (see lib/trmnl/include/http_client.h).
//  - The device and server must be on the same network. If your machine's
//    LAN IP changes, update it here and reflash.
//  - No port? Append the port the server listens on, e.g. :8080.
//
//  A ready-to-run reference server lives in:  byos/server.py
//  (run it with:  ./byos/server.py )
// ============================================================================

#define BYOS_SERVER_URL "http://192.168.1.107:8080"

#endif // BYOS_CONFIG_H

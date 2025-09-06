
# Rwt
A web based terminal emulator that can be accessed remotely with support for token security

# About
**NOTE: A majority of the code is generated via ChatGPT**

**Available arguments**
 - `--port` sets the port
 - `--host` what address to bind to
 - `--shell` what shell to use
 - `TERMINAL_TOKEN` override token (this is a environment variable)

**dependencies:**
 - Python 3
 - aiohttp
 - pywinpty (only for Windows)

**OS support:**

 - Windows - should work, untested
 - Linux (Debian 12 with bash) - all features function, tested
 - MacOS - should work, untested
 - iOS (A-shell terminal emulator) - Somewhat functional, everything works except the shell, it could probably be worked around, tested 
 - BSD - should work, untested


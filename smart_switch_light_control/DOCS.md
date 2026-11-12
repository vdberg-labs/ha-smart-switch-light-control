# Smart Switch Light Control

Open this app's web UI (or **Smart Switch** in the sidebar, if you enable it on the Info tab).

Choose a switch and press **Add switch**; each switch can only be added once. Turn on the buttons you want
this app to handle, then pick their gestures and search for the lights, areas and labels each one
controls. Saving applies the changes immediately.

While a button is held, the app sends a brightness command every update interval and waits for each one to
finish before sending the next. If dimming lags or flickers (Zigbee lights in particular, or many lights on one
button), open the button's **Advanced** section and raise the update interval and transition together, for
example to 500 ms each. Leave a field empty to use its default.

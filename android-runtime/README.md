# Android pool reference

This runtime unpacks an immutable Redroid OCI image into LXC, assigns one Binder device triple and private IP per Android instance, and limits CPU, memory, swap and processes. Android and an optional Debian userspace inside that instance share the same LXC boundary. The retained image pin is Android 14; it is not a claim of the newest supported Android release.

The pool requires a dedicated Debian amd64 VM with cgroup v2, LXC, AppArmor, Binder, TUN, BusyBox, Python, ADB, dnsmasq, iptables, skopeo, umoci, jq and debootstrap. Install prerequisites and inspect host configuration before using `bootstrap-host.sh --isolated-pool`; it changes kernel modules, networking and systemd units. It must run only inside the dedicated Android VM. It requires its Binder layout to be free and refuses to unload a conflicting one.

The examples use Android subnet `10.77.0.0/24`, gateway `10.77.0.1`, and private website ingress `10.77.1.10`. Choose unused networks and update every matching reference together. ADB and the screen gateway stay private. Rooted Android shares the pool VM kernel; it is not an independent virtual-machine boundary.

`brandfleet-android.py` validates ownership, reserves capacity and runs fixed lifecycle commands. `pool-lifecycle.py` applies boot and shutdown ordering. `native-pre-stop.py` has a fixed FreeScout queue-drain hook; the helper must be installed inside the associated Debian userspace before enabling it. `screen-gateway.py` exposes an allowlisted capture/input/app-launch API on loopback. `social-evidence.py` reads already collected package proofs and performs no Android commands; configure its example IDs, catalog and proof files independently. APKs, social sessions, Android image layers and one-off qualification/migration tools are not included.

For a different Android image, the provisioner permits offline preparation with `BRANDFLEET_ANDROID_NO_START=1`, but refuses direct boot of an unqualified image. Qualify new images and application compatibility separately before changing this guard. Runtime metadata and archives remain private, outside Git. The archive command retains persistent data; native application restoration requires its own verified deployment and database restore plan.

Run these offline checks from the repository root:

```sh
python3 -m unittest discover -s android-runtime -p 'test_screen_gateway.py' -v
python3 android-runtime/test-image-cache.py
python3 android-runtime/test-provision-guard.py
python3 android-runtime/test-native-readiness.py
python3 android-runtime/test-native-pre-stop.py
```

These checks use mocked runtime commands and temporary data. They do not boot Android or validate a production deployment.

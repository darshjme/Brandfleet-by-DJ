# The Android hosting experiment

This document records the Android hosting experiment accepted on **6 October 2026**. It is historical: the current deployment direction uses [Coolify on Debian](architecture.md) for websites, with Android data retained for recovery rather than coupled to website uptime.

![Historical Android hosting architecture](assets/architecture-android.svg)

## Traffic and control

Public website traffic follows domain DNS to a TLS ingress, then to a brand's native website processes. Many domains can share the ingress address because the reverse proxy selects a route by hostname. A shared address does not mean that all applications run in one container.

An authenticated administrator uses the Node dashboard. The dashboard passes fixed lifecycle requests to a private Python controller, which maintains inventory and job state. Android screenshots and input go through a separate private gateway. ADB, bearer credentials and internal control endpoints stay behind the gateway rather than becoming public browser endpoints.

Each brand's Android LXC contains two kinds of persistent state: Android userdata and the website's native Linux userspace. The website processes run inside a Debian userspace chroot within that container. A chroot supplies a Linux filesystem and libraries; it does not supply another kernel or independent container boundary. This coupling makes the brand easy to address as one unit, but stopping the Android LXC also stops its website.

## Isolation has several layers

| Layer | What it isolates | Remaining common dependency |
| --- | --- | --- |
| Per-brand LXC | Userspace, persistent paths, network namespace and assigned resource limits | Android pool kernel and physical capacity |
| Android pool VM | Android kernel and pool services from the ingress/shared-services VM | Physical Proxmox host |
| Shared Debian LXC | Mail, workspace and shared backends from per-brand devices | Its containing host/VM and storage |
| TLS ingress | Public routing from private device-control endpoints | Ingress availability and route configuration |

The reference did not run a Docker daemon inside the Android devices or shared Debian LXC. The retained ingress platform still supplied Traefik. LXC isolation and a VM boundary do not turn Android social applications into ordinary lightweight website workers.

## Resource admission

The recorded pool VM had **42 GiB RAM**. Fifteen live devices had assigned limits totalling **33,280 MiB**, against a measured allocation budget of **38,037 MiB**. These numbers describe one host at one acceptance point, not minimum specifications or a throughput benchmark.

The allocation budget was the smaller of 38 GiB and measured guest memory minus a 4 GiB OS reserve. Creation also checked actual available memory: a default 2 GiB device needed 5 GiB available, preserving a separate 3 GiB admission reserve. Thirty-two prepared binder slots defined a slot ceiling; they did not establish that 32 devices could fit. Admission must remeasure the host rather than extrapolating from the slot count.

Android services, rendering, social apps and per-brand databases compete for real CPU and memory. Resource caps can contain one brand's usage, but cannot remove Android's base cost. That cost and unresolved app compatibility motivated the requested return to conventional website deployment.

## Browser interaction and responsiveness

The device studio offers fixed launch controls for Instagram, WhatsApp, LinkedIn, X and Figma Mirror, plus touch, swipe, text entry, Home, Back and Recents. It displays PNG snapshots rather than an accepted continuous video stream.

The viewer avoids decoding identical PNGs and reduces capture demand after repeated unchanged frames. Input, app launch or a visible change resets the interval. Hidden or paused viewers stop polling, and control ownership is bounded per device. A gateway-wide capture limit prevents a browser tab from creating unbounded fresh work. These controls reduce needless capture/decode work; they do not prove GPU acceleration, smoother video or lower production CPU usage.

The reference optimization halved Android window and transition animation scales. It preserved website process identities and device resource limits. Production acceptance included the dashboard and controller suites, real browser Instagram launch and Home input, and all fifteen website origins. The source release is tested separately after sanitization.

## Shared services

A separate Debian LXC hosted Nextcloud Files, Talk, Calendar, Contacts and Deck; native Postfix and Dovecot with filtering, antivirus, DKIM, Sieve and webmail; and shared backends including TURN. Its recorded limit was 4 GiB and two CPUs.

A shared workspace instance needs explicit identity, group and access-control configuration for each organization. It should not be described as independently isolated tenant systems merely because it serves several domains. Likewise, mail domains need explicit administration plus verified MX, SPF, DKIM, DMARC and reverse DNS. Domains already using an external workspace provider can keep their existing mail routing.

The fixed-mailbox webmail broker is intentionally narrow. It issues a short-lived, single-use grant for a configured mailbox and relies on normal TLS IMAP authentication. It is not a general Google Workspace login system. Operators must configure its principal, mailbox and private issuer material for their own environment.

## Accepted limits

The production snapshot used Android 14/API 34 with a May 2024 security patch. An Android 16 canary failed bootstrap APEX mounting under the tested isolation and was stopped. No production OS upgrade was established.

Instagram and WhatsApp reached their welcome screens. LinkedIn foreground launch worked, but usable sign-in remained unresolved; WhatsApp displayed a custom-ROM warning. No connected-account publishing was accepted. A home exit node was advertised, while administrator approval and app-specific Android egress acceptance remained pending. Routing never establishes that a platform cannot detect automation.

A source checkout cannot recreate logged-in Android devices, original mailboxes or application databases. Those require separate authorized backups and restore verification. Mail protocol and TURN relay tests also have narrower scope than outside mail deliverability or an actual browser-to-browser call.

## What changed

The return to Coolify separates website processes, persistent databases and workers from the Android runtime. The retained experiment remains useful reference code for isolated device control; it is not the production website hosting model. See the [current architecture](architecture.md) and [operating model](operations.md).

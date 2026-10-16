# Haiku / BeOS Agent Gotchas — Mistakes & Dead Ends to Not Repeat

> [!IMPORTANT]
> **AI Instruction:** This is a curated list of real mistakes, dead ends, and Haiku/BeOS quirks that AI agents
> repeatedly got wrong while building the MIDI apps (`Media-OS`, `MidiMonitor2`, `pocketcalc`) and the media
> player (`BeTon`). **Read this before starting Haiku work.** Each item cites the commit(s) that fixed it, or the
> Haiku revision / date it was verified against, so you can read the full context and judge whether it has aged.
> Verify against the Haiku source when in doubt — and see §13 before trusting a local checkout's history.
>
> **Scope:** everything here is about **Haiku/BeOS itself** and applies from any dev host. Quirks of a
> particular dev host's shell or tooling do not belong here.
>
> **Last reviewed 2026-10-16.** Items marked *(observed)* were seen on specific hardware and are not yet
> explained from source — treat them as warnings, not as established Haiku behaviour.

---

## 1. GCC 2.95 / 32-bit legacy toolchain — the #1 source of dead ends

**32-bit Haiku (x86_gcc2)** builds with **GCC 2.95 (C++98)**. Modern GCC (64-bit) tolerates things GCC2 rejects,
and vice versa, so **a 64-bit build proves nothing about 32-bit.** Write gcc2-clean code from the start so the
port stays cheap, and **build on x86_gcc2 before claiming 32-bit support or shipping a 32-bit package**
(`BeTon e345f9b`: *"I should really check 32bit builds before commit anything..."* — this happened more than once).
Projects developed x86_64-first need not gate every commit on a 32-bit build; the rule is about claims and
releases, not commits.

- **No C++11.** No lambdas — extract a `static` helper function instead. *(Media-OS `3607f7d`)*
- **No zero-initialization of POD members** in a struct that also has `std::string` members. GCC2 C++98 leaves
  primitive members uninitialized, so a bare `bool has_bcd;` reads as garbage. **Explicitly init every primitive**
  (`has_bcd = false; bcd_device = 0;`). This silently skipped every config rule until fixed. *(Media-OS `b271044`)*
- **`-Wextra` is unsupported** by GCC 2.95 — drop it from `CXXFLAGS`. *(Media-OS `d7ee9c0`)*
- **Explicit casts** needed where modern GCC infers; e.g. casting debug-message args satisfies the 32-bit build.
  *(BeTon `5d4881e` "explicit cast debug messages to satisfy 32bit build")*
- **iostreams differ:** legacy GCC2 wants `<iostream.h>`/`<fstream.h>`; modern wants `<iostream>`. For dual-compiler
  support, replace deprecated `.h` headers with modern standard headers and guard usage. *(pocketcalc `a6f0347`, `3c0add6`)*
- **`errno` is not implicitly declared** — `#include <errno.h>` explicitly. *(Media-OS `f31c54f`)*
- **String literals are `const char*`, so declare `name` params `const char*`.** Be API constructors
  (`BView`/`BWindow`, etc.) take `const char* name`, but old BeOS sample code routinely declares custom
  view/window subclass constructors as `char* name`. Passing a string literal then triggers GCC2's
  *"deprecated conversion from string constant to `char*`"* warning — and is a hard **error** on modern
  64-bit GCC. Match the kit: declare your own `name` params `const char*`. *(MidiMonitor2 `7104b85`; the
  same `char* name` pattern is still live in `pocketcalc/AboutBoxView`.)*

### API signatures that differ between GCC2 / modern Haiku
- **`BEntry::GetName(char* buffer)`** — on GCC2 there is **no size argument**. *(Media-OS `3dd8a47`)*
- **`StopWatching()`** takes **no arguments** on Haiku. *(MidiMonitor2 `52da2b4`)*
- **`BString` substring extraction:** use **`CopyInto`**. *(BeTon `64e735b`)*
- **`BUrl`** behavior/signatures vary across versions — verify against the target. *(BeTon `a7b979b`)*

---

## 2. Build system — makefile-engine & resources

- The generic Haiku app Makefile template lives at the **`makefile-engine`** path; point the include there.
  *(pocketcalc `be4b792`)*
- **Force the C++ compiler/linker:** set **`COMPILER = g++`** and **`LD = g++`**, or you get C++ standard-library
  linker errors. *(pocketcalc `0a77499`, `cdcb417`)*
- **Resources — `RDEFS` vs `RSRCS`:** `RDEFS` = text `.rdef` compiled by `rc`; `RSRCS` = prebuilt binary `.rsrc`.
  **Prefer the text `.rdef` checked into git**; there was a lot of churn flip-flopping between the two and committing
  stale/missing binaries. *(pocketcalc `759b8c1`, `627af42`, `ecae6ec`, `2807e61`)*
- **`.rdef` syntax is strict** (e.g. the `long_info` field) — a malformed field breaks `rc`. *(pocketcalc `1a5e002`)*
- On Haiku, **embed resources after link** with `rc -o x.rsrc x.rdef` then `xres -o <target> x.rsrc` (guard so
  off-Haiku builds skip it). *(Media-OS `8c9fbf2`, Makefile)*
- **jam's timestamps are second-granular.** Re-applying a patch and rebuilding within the same second leaves the
  source and the object with equal mtimes, and jam silently skips the recompile — the module links, but from the
  old code. After any `git apply` / checkout between builds, `touch -d "+5 seconds"` the changed sources, and
  compare the built binary against the expected one (`cmp`, or a string unique to the change) before deploying.
  *(2026-10-15, twice in one session)*

---

## 3. Headers & forward declarations

- **Flatten includes:** prefer `<Application.h>`, `<Locker.h>` over nested `<app/Application.h>` (Be Book legacy style).
- `BWindow`/`BMessage` are **forward-declared** in many headers. When you actually *use* them, include `Window.h`
  and `Message.h` or compilation fails. *(pocketcalc `b3f7c9f`)*
- **Modern GCC (64-bit) needs explicit UI header includes** that GCC2 tolerated implicitly. *(pocketcalc `752659e`)*

---

## 4. BApplication / server lifecycle & threading

- **`BMidiLocalProducer`/`BMidiLocalConsumer` can only be created safely on the main `BApplication` thread.**
  Post a message to `be_app` and do the MIDI-kit work in the main thread's handler. *(Media-OS `34de0ab`)*
- **Don't start rosters / register endpoints in a constructor.** Do it in **`ReadyToRun()`**, after the message loop
  is live. Calling `BUSBRoster::Start()` / `BMidiLocalProducer::Register()` too early **deadlocks**: the BUSBRoster
  thread blocks waiting on a reply from a message loop that isn't processing yet. If `Start()` may fire `DeviceAdded`
  synchronously, **spawn a helper thread** so the main thread stays free to service `MSG_REGISTER_APP` /
  `MSG_CREATE_ENDPOINT`. Mirrors stock Haiku `DeviceWatcher._InitialDevicesScanThread`. *(Media-OS `b271044`, `4573846`, `5288d1a`, `8f384b2`)*
- **`B_EXCLUSIVE_LAUNCH` → `B_SILENT_RELAUNCH`.** When a second instance launches, `launch_daemon` sends
  `B_SILENT_RELAUNCH`, and the default handler calls **`ReadyToRun()` again** → duplicate init / racing threads /
  MIDI IPC timeouts. **Handle and suppress it.** *(Media-OS `4573846`)*
- **Reference-counted `BMidiEndpoint`s:** use **`Release()`**, never `delete`. *(Media-OS `2839959`)*
- **Background servers need no locale/translation macros** — drop them. *(Media-OS `850589f`)*

---

## 5. Interface Kit quirks

- **`BRect::OffsetToSelf()` mutates in place** and returns void-ish semantics — don't use it where you expect a fresh
  offset rect; it corrupted layout positioning. Use `OffsetByCopy`/compute a new rect. *(pocketcalc `a87becc`)*
- **`BTextView` re-entrancy:** reformatting text *synchronously* from within a notification/update re-enters the view
  and breaks. **Post an async message** (e.g. `MSG_FORMAT_HEX`) and format on the next loop. *(pocketcalc `794c619`)*
- **`ScrollTo(const BRow*)` (BColumnListView/OutlineView) scrolls *minimally*** — it brings a row to the *bottom* edge,
  not the top. After a rebuild (view at top), a saved top-row ends up at the bottom. **Compute the row's content-space
  y and `ScrollTo(BPoint)`** to put it back at the top. *(BeTon `4b2be9d`)*
- **`BColumnListView::RowAt(BPoint)` / `FindRow` take content-space y** — offset by the outline view's scrolled
  `Bounds().top` when probing visible rows. *(BeTon, verified from `ColumnListView.cpp` at hrev57937)*
- **Window-level shortcuts** (e.g. Alt+A select-all) must be registered with **`AddShortcut()`** on the window, not
  just handled in a view. *(BeTon `7c3bf55`)*
- Replacing **`B_AUTO_UPDATE_SIZE_LIMITS`** can regress window sizing — re-check resize behavior afterward.
  *(BeTon `dc98d5e`)*

---

## 6. Tracker scripting (verified empirically on Haiku x86_64 hrev57937)

Getting Tracker to select a file via scripting is fiddly; the wrong message shape silently returns
`B_MESSAGE_NOT_UNDERSTOOD`:

- **To select files:** send `B_SET_PROPERTY` with specifiers `Selection` → `Poses` → `Window <i>`, and put the
  `entry_ref`s **top-level** in the message as **`data`** (`msg.AddRef("data", &ref)`), with a plain
  `AddSpecifier("Selection")`. Refs placed *inside* the specifier message get `B_MESSAGE_NOT_UNDERSTOOD` ('MNOT').
  *(BeTon `d921641`, which reversed the earlier `ccb6913` — that commit put refs in the specifier and was wrong.
  An earlier version of this doc described `ccb6913`'s shape; corrected 2026-10-16.)*
- **Read the selection back with `hey`** (`hey Tracker get Selection of Poses of Window N`) to verify without looking
  at the screen. Folder window titles are full paths; the "Tracker status" and Desktop windows have no `Poses`.
- **When unsure of a scripting message shape, don't iterate blind:** write a ~50-line standalone test
  (`g++ -o t t.cpp -lbe`), send the real message and `PrintToStream()` the reply. Recollection of Tracker's source
  was wrong twice here; only an on-box test settled it.
- **The `Path` property of `Poses` replies with an `entry_ref` named `result`** (not a string) — resolve it via
  `BPath` before comparing directories. *(BeTon `ccb6913`)*
- **Poll for the freshly-launched Tracker window** (e.g. 8× at 300 ms) instead of a single fixed wait. *(BeTon `ccb6913`)*

---

## 7. USB (BUSBKit vs libusb, raw driver)

- **Prefer native BUSBKit** (`BUSBRoster`/`BUSBDevice`/`BUSBEndpoint`, link `-ldevice`) over libusb: no x86 ABI
  dependency, GCC2-compatible, and clean hotplug via `DeviceAdded`/`DeviceRemoved`. The long libusb detour
  (firmware loaders, endpoint halts, alt-interface fights) was largely a dead end. *(Media-OS `52d4709`)*
- **The Haiku USB raw driver validates the data pointer on *all* control transfers, regardless of length.** A
  zero-length transfer (e.g. `SET_FEATURE(PORT_RESET)`, `wLength=0`) with a `NULL` data pointer → **`B_BAD_ADDRESS`**.
  Pass a dummy stack byte as the pointer; also clear `errno` before the `ioctl`. *(Media-OS `2c90c6b`)*
- `DeviceRemoved` must call teardown **synchronously** — the device pointer is only valid until it returns.
  *(Media-OS `34de0ab`)*

---

## 8. Preprocessor / portability

- **Mind `#ifdef __HAIKU__` / `#else` / `#endif` balance** — an `#endif` landing inside the `#ifdef` instead of the
  `#else` branch silently miscompiles. *(Media-OS `cdde47f`)*
- When cross-developing off-Haiku, gate Haiku-only kit links behind `uname` (`Haiku`/`BeOS`) in the Makefile so the
  tree still parses elsewhere. *(Media-OS Makefile)*

---

## 9. Replacing stock system components (packagefs, non-packaged, Deskbar)

When shipping a replacement for a stock Haiku binary (driver, server, app), three layering facts decide what
actually takes over. Getting these wrong makes the stock copy keep loading alongside yours (e.g. every MIDI device
showing up twice). *(Media-OS, 2026-06-22.)*

- **`/boot/system/non-packaged/` is a SEPARATE subtree, not an overlay.** Dropping a file at
  `non-packaged/servers/midi_server` does **not** shadow `/boot/system/servers/midi_server` — that path still
  resolves to the packaged binary. Non-packaged dirs are *added to search paths* (PATH, kernel add-on dirs, the
  Deskbar menu, etc.), but the top-level `/boot/system/<x>` namespace is unchanged. So "just put it in
  non-packaged" is not enough to displace a stock binary that something loads by fixed path.
- **To hide a stock (packaged, read-only) file, use the packagefs blocklist.** Create
  `/boot/system/settings/packages` (driver_settings format; the path is the packagefs mount-relative
  `settings/packages`, per `PackageSettings::Load` in the packagefs source):
  ```
  Package haiku {
      BlockedEntries {
          "add-ons/kernel/drivers/bin/usb_midi"
          "servers/midi_server"
      }
  }
  ```
  Entry paths are package-relative (= relative to `/boot/system`). Find the owning package + exact paths with
  `package list -p /boot/system/packages/haiku-*.hpkg | grep <name>`. **Read only at mount → reboot to apply.**
  Bad syntax is silently ignored (won't break boot). Blocklisting a **boot module** makes the boot loader log
  `Could not load "<module>" error 80006003` (`B_ENTRY_NOT_FOUND`) on every boot — expected, not a failure.
- ⚠️ **Reinstalling Haiku over an existing system keeps `/boot/system/settings`**, blocklist included. If the
  replacements lived in `non-packaged` and are gone, the fresh system boots with the stock files still hidden:
  no USB, no media/MIDI servers. Delete or check `settings/packages` after a reinstall. *(observed once,
  2026-10-14)*
- **A non-packaged legacy driver does not displace the stock one** — devfs scans the driver dirs and loads
  everything it finds, so a stock driver and your non-packaged one *both* load (devfs logs two `loaded driver …`
  lines then a `devfs: reload driver` reconcile that is order-dependent/racy). Blocklist the stock driver so only
  yours loads. Two other routes exist: a package that wins packagefs precedence over the stock file (§10), and,
  for **boot modules** only, `~/config/non-packaged/add-ons/kernel/boot/` (§11). ⚠️ A blocklisted stock driver
  with a broken replacement means **no driver at all** — e.g. `usb_disk` is the only USB storage driver.
- **Servers are displaced via launch_daemon, not (only) the blocklist** — but **only from the system launch
  directories** (`/boot/system/settings/launch/` or `/boot/system/non-packaged/data/launch/`).
  `~/config/settings/launch/` is read only by the *user* launch_daemon and cannot touch a system service.
  The blocklist alone does **not** start your server; the launch override is what performs the swap.
- ⚠️ **Never re-declare a stock job name to change its `launch` path.** A second definition *amends* the existing
  job, and `_AddJob` appends the `init` requirement again with no de-duplication; `Job::Init` sees the duplicate as
  a **cyclic dependency**, returns `B_ERROR` (`Init "…" failed` in the syslog) and `_InitJobs` **deletes the job**
  (`launch_roster start` → "Name not found"). The working pattern is two definitions with different names:
  ```
  service x-vnd.Haiku-midi_server { disabled true }                 # stock job: disable only
  service x-vnd.MyProject-midi_server {                             # yours: a NEW, unique name
      launch /boot/system/non-packaged/servers/midi_server
  }
  ```
  Clients still find yours because they resolve the *signature*, which your binary registers. *(Observed on
  hrev57937; code path re-verified on r1beta6 `874f0cea9d`, `LaunchDaemon.cpp` `_AddJob` / `_InitJobs`.)*
- **launch_daemon reads job files only at boot** — there is no reload, so a new job cannot be tested in the
  current session. Verify with `launch_roster info <job>` (enabled / running / team).
- ⚠️ **A `BServer` with a launch job must NOT be handed a port it created itself.** Pass `-1` (or use the 3-arg
  `BServer(signature, initGUI, &error)`). `BMessenger`'s signature constructor asks **`BLaunchRoster` first** for the
  job's port and only then falls back to the registrar (`Messenger.cpp`), while `BApplication` only adopts the job
  port when its `port` argument is negative (`Application.cpp`). Pass your own port and the looper reads it while
  every client writes to the job's — messages queue unread and **every client hangs, silently**. One-command
  diagnosis: `listport <team>` shows **two** looper-sized ports, the job-named one with a non-zero `queued`.
  Exception: `legacy` jobs (media_server, net_server) get no default port and do create their own. If you need a
  fallback for hand-started dev instances, gate it on `BLaunchRoster().GetJobInfo()` — **not** `GetPort`/`GetData`,
  which force-launch an unlaunched service. *(Media-OS, measured 2026-08-30 on hrev59866+79)*
- **The Deskbar menu is a priority-merged VIRTUAL DIRECTORY.** `/boot/system/data/deskbar/menu_entries` has MIME
  `application/x-vnd.haiku-virtual-directory` and lists several real dirs in priority order (highest first):
  ```
  /boot/home/config/settings/deskbar/menu        (user, usually empty)
  /boot/home/config/non-packaged/data/deskbar/menu
  /boot/home/config/data/deskbar/menu
  /boot/system/non-packaged/data/deskbar/menu     ← writable, system-wide
  /boot/system/data/deskbar/menu                  ← stock, read-only, lowest
  ```
  Same-named entries are **shadowed by the higher-priority dir**. So to replace a stock menu item (e.g. the
  read-only `Demos/PatchBay` symlink) with your build **without a blocklist**: put a same-named link in a
  higher-priority dir, e.g. `ln -s <your-binary> /boot/system/non-packaged/data/deskbar/menu/Demos/PatchBay`. One
  entry shows (yours), no duplicate; removing it restores the stock entry. (The writable user
  `~/config/settings/deskbar/menu` is empty by default — the menu you see comes from the stock tree, so adding the
  *same name* there or in the non-packaged system dir overrides it.)

---

## 10. Packaging (`.hpkg`, `pkgman`, repositories)

Every item here fails **silently** — the package installs, the file is visible, and nothing says it is inert.

- **Package scripts run only if `.PackageInfo` declares them.** Staging a file into `boot/post-install/` (or
  `boot/pre-uninstall/`) only puts it in the payload. `CommitTransactionHandler` iterates
  `Info().PostInstallScripts()` / `PreUninstallScripts()` — undeclared means never executed, no log line:
  ```
  post-install-scripts {
      "boot/post-install/my_script.sh"
  }
  ```
  Check the **attributes**, not the payload listing: `package list -i <pkg>.hpkg | grep -i post-install`.
  *(Media-OS, 2026-08-19, haiku `9189702b`)*
- ⚠️ **Never rely on a pre-uninstall script for anything safety-critical.** By the time it runs, the old `.hpkg`
  has already been moved out of `/boot/system/packages`, so the script runs only if packagefs still has its data
  cached — otherwise `_RunPostOrPreScript … failed: 127` (`No such file or directory`). It ran on some upgrades and
  not on others the same day. The boot menu's package-state rollback never runs them at all (it opens no
  transaction). Put safety steps in **post-install** (read from the newly activated package) and in boot-time
  launch jobs. *(Media-OS, 2026-10-12, hrev59866+88)*
- ⚠️ **Never `pkgman install` the same version over itself.** The new `.hpkg` has the same file name as the active
  one, the old package vanishes before its pre-uninstall script can be found, and the transaction proceeds with
  that step skipped. Uninstall first, or bump the revision. Related: an uninstall leaves the old `.hpkg` in
  `administrative/state_*`, and a later reinstall of the **same version** can abort with
  `Interrupted system call` (HTTP 416 from the repo). Moving the stale same-name files aside cleared it.
  *(Media-OS, 2026-10-06 / 2026-10-12)*
- **Overriding a stock file needs BOTH `flags { system_package }` AND a newer mtime than stock's.** Measured as a
  full 2×2 against an inert stock file (a license text) — any other combination, stock wins. And **`package create`
  copies each file's mtime from the source on disk**; it does not stamp build time. So bumping the revision alone
  ships the same old mtime and loses: `touch` every payload file right before `package create`, *and* bump the
  revision (so `pkgman` has a reason to update). *(Media-OS, measured 2026-08-17/19 on hrev59866+53)*
- ⚠️ **Any Haiku update reverts every packaged file that overrides a stock one** — even an update that changes
  nothing you touch, because the rebuilt `haiku` package re-stamps all its mtimes. You must republish after every
  hrev bump just to win the mtime race back. Non-packaged files are not in the race. Don't re-derive this from
  source: `package list -i` shows `haiku`'s `flags:` as empty, which suggests a flagged package beats it regardless
  of mtime — the experiment says otherwise. *(Media-OS, field-confirmed 2026-08-25, hrev59866+53 → +79)*
- ⚠️ **Repository priority is INVERTED — a LOWER number wins.** `LibsolvSolver.cpp` passes `-1 - Priority()` to
  libsolv, which prefers the larger value. The default, 50 (`BRepositoryInfo::kDefaultPriority`), puts a third-party
  repo **below** Haiku/HaikuPorts (1), and priority is settled **before** version — so a same-named HaikuPorts
  package beats yours however new yours is. Use `priority 1` to tie and fall through to version comparison (0
  collides with `kUnsetPriority`). A priority change **reaches no existing subscriber** — it is copied into the
  local config only at add-repo time; they need `drop-repo` + `add-repo`. *(BeTon `a83fbe8`, 2026-09-01)*
- **The MIME database is `~/config/settings/mime_db`**, not the BeOS-era `beos_mime`. And **`mimeset -a` will not
  fix a stale app hint (`META:PPATH`) — it needs `-f`** (`AppMetaMimeCreator.cpp` writes the hint only if forced or
  absent). Consequence: **`BAboutWindow` shows the version of whatever the app hint points at**, not the running
  binary, so a reinstall can keep reporting an old version. Check with `version -n <path>`, fix with
  `mimeset -a -f <app>`, then relaunch. *(BeTon, 2026-08-29, hrev59866+53/+79)*

---

## 11. Kernel add-ons, drivers & the USB stack

- **Renaming a driver to `foo.disabled` / `foo.bak` does NOT disable it.** Add-on lookup matches directory entries
  by **prefix** (`ModuleNotificationService::_ScanDirectory`: `strncmp(d_name, prefix, length)`), so every suffixed
  copy beside the real one is still a candidate for the same module — a per-boot coin flip. Keep backups
  **outside** the add-ons tree, and before any A/B test confirm exactly one file per module name.
  *(Media-OS, 2026-08-06)*
- **Boot modules** (everything in `/boot/system/add-ons/kernel/boot/`: `ahci`, `bfs`, `pci`, `acpi`, the USB host
  controllers, …) are pre-loaded by the boot loader before packagefs exists:
  - ✅ **`~/config/non-packaged/add-ons/kernel/boot/` wins over stock, no blocklist needed** (measured).
  - ❌ `/boot/system/non-packaged/add-ons/kernel/boot/` is **never scanned**: the loader strips the `system/` prefix
    and looks the path up *inside `haiku.hpkg`*, where it does not exist (`boot/loader/loader.cpp`,
    `open_maybe_packaged()`; `vfs.cpp` `BootVolume::SetTo()`).
  - ❌ A third-party `.hpkg` can't do it either — the loader mounts exactly one package, matched by file name.
  - ⛔ Blocklisting a boot module without a working replacement → `PANIC: did not find any boot partitions`.
  *(Media-OS, measured 2026-09-22 on hrev60072+55; an earlier note claiming boot modules could not be overridden
  at all was retracted the same day.)*
- ⚠️ **The boot menu's "Disable user add-ons" disables everything outside the *packaged* system tree** — not just
  `/boot/home`. `/boot/system/non-packaged/add-ons`, `~/config/add-ons` and `~/config/non-packaged/add-ons` are all
  skipped (`module.cpp` `kFirstNonSystemModulePath`; `legacy_drivers.cpp`). If you have blocklisted stock drivers in
  favour of non-packaged ones, this leaves **no driver at all** (e.g. no USB keyboard). It is not an isolation tool:
  move the binary out instead. *(Media-OS, 2026-08-11)*
- ⚠️ **Build kernel add-ons against the SAME BRANCH as the running kernel.** `master` sets `KDEBUG_LEVEL 2`,
  `r1beta6` sets 1, so a module built on master turns on `ASSERT_LOCKED_*` macros the Beta 6 kernel compiles out —
  they then read `struct mutex`'s `count` field as `holder` and **KDL on byte-identical stock source**. Before
  believing an `ASSERT` failure in stock code, check the asserted condition by hand. To check a binary:
  `strings <module> | grep -c 'ASSERT FAILED'` (KDEBUG-2 builds carry two format strings; `find_thread` counts do
  **not** discriminate). A residual `1` in host controllers is `ASSERT_ALWAYS` and is correct.
  *(Media-OS, 2026-09-07, r1beta6 `874f0cea` = hrev59866+79)*
- **Beta 6 can't link kernel add-ons out of the box:** `ld: cannot find …/libgcc-kernel.a` /
  `libsupc++-kernel.a`. The compile succeeds, so it looks like an API break — it is a missing package:
  `pkgman install gcc_syslibs_devel`. Don't repoint scripts at the copy under `develop/tools/lib/gcc/…`.
  *(Media-OS, 2026-08-10, hrev59866+53)*
- **A fresh Beta 6 sets `serial_debug_output false`** in `~/config/settings/kernel/drivers/kernel` — serial capture
  is dead until you set it `true` and reboot. *(2026-08-10)*
- **A non-packaged `usb` bus manager shadows the stock one for the WHOLE USB stack.** A bad one leaves devices
  enumerating normally (`listusb` correct) while **nothing binds**, with no error. Diagnostic: count
  `usb_serial: probing device` lines in the boot log *with a bindable device attached* — `usb_serial` probes every
  device, so zero means the driver-offer pass isn't running. (`usb_hid: no handlers for hid device` is a *healthy*
  sign; plain `probing device` also matches ATA.) *(Media-OS, 2026-08-03 / 2026-08-10)*
- **The `Hub` class couples the bus manager to all four host controllers.** Each `*RootHub` derives from `Hub` and
  is allocated inside the controller at its compile-time `sizeof(Hub)`, and every controller statically links its
  **own** copy of `Device.cpp`, `Hub.cpp`, etc. So: whenever `usb_private.h` changes, rebuild and deploy `usb`,
  `uhci`, `ohci`, `ehci` and `xhci` **together from one tree**, and know that swapping the bus manager alone tests
  none of its `Device`/`Hub` code. *(RenkuOS `c7942d0ed7`, 2026-09-28; `fdb7f13c5a`, 2026-10-07)* The same holds
  for **`Stack.cpp`**: each controller carries its own copy of the explore code (a KDL backtrace shows
  `Stack::Explore` running from the `ehci` image), so a change there is inert until the controllers are rebuilt and
  deployed as well — check with `nm -D <controller>` for a symbol the change introduced. *(2026-10-16)*
- ⚠️ **A host controller loaded from `non-packaged` (stock one blocklisted) starts AFTER the boot volume is mounted**
  — a path a normal boot never takes, since the stock controllers are boot modules. Stock `Stack::Explore()` runs
  inline in the controller's `Start()` under the device manager lock; after the mount, a USB disk that enumerated
  on a UHCI/OHCI companion and is then taken back by EHCI gets unpublished from there, and the boot **deadlocks**:
  `main2` holds "device manager" and waits for "devfs scan", held by a `device event` thread
  (`KDiskDeviceManager`) waiting for "device manager". Test controller builds without a USB disk attached at boot,
  or explore from the explore thread once `gBootDevice >= 0`. *(Haiku32 ICH7 2026-10-03; ICH10 2026-10-16, KDL)*
- **Add members to a stock host-controller class at the END.** Inserting mid-class shifts offsets other modules
  were compiled against. A broken EHCI also takes out every full/low-speed device behind it, because EHCI hands
  those ports to its UHCI/OHCI **companions** — grep the boot log for `giving up port ownership` to confirm
  handoff works. *(Media-OS, bisected 2026-08-04)*
- **Driver settings are read in `init_driver()`** — a replug does not reload the driver; changing them needs a
  reboot. **`usb_audio`'s `trace` is a BITMASK**, not a level (`ERR 0x01 INF 0x02 MIX 0x04 API 0x08 DTA 0x10
  ISO 0x20 UAC 0x40`): `trace 1` is errors only; use `3` for stream diagnosis; `DTA`/`ISO` log per transfer and will
  starve a 115200 serial console. *(Media-OS, 2026-08-18, `Settings.h`)*

---

## 12. Media Kit

- **The decoder does NOT resample.** `BMediaTrack::DecodedFormat()` converts channel count and sample format but
  pins `frame_rate` to the file's native rate; rate conversion lives only in the system **mixer**. Connect a
  `BSoundPlayer` straight to a hardware output node and a 44.1k file plays fast and high on a 48/96k device. Resample
  yourself (e.g. `libswresample`). *(BeTon, verified from source at hrev57937)*
- **The mixer's gain sliders are a power curve, not linear dB.** `AudioMixer::dB_to_Gain()` warps the displayed dB
  (`DB_EXPONENT_POSITIVE 1.4`, `DB_EXPONENT_NEGATIVE 1.8`) before `10^(db/20)`: displayed −39.7 dB ≈ −28.5 dB real.
  The mixer also applies **"Attenuate mixer output by 3 dB" (×0.708), on by default**. A direct connection bypasses
  both; replicate them to match loudness. *(BeTon, hardware-measured)*
- **Node run-state is not reference-counted.** `BMediaRoster::StartNode` only posts `NODE_START` and returns; two
  clients start, one stops → stopped for both. **`multi_audio`'s `StopNode` does not stop its output thread**
  (`_StopOutputThread()` is commented out in `_HandleStop`), and the consumer-side `Connected()` path does not
  start it — a direct connection must call `StartNode` explicitly and allow for the asynchronous start, or the
  first buffer goes nowhere ("first play does nothing, second works"). *(BeTon `7085f8a`, source at hrev57937)*
- **Changing the default audio output/input does not move a running system mixer** — restart the media server
  before measuring. *(Media-OS, 2026-10-07)*
- ⚠️ **`BTimeSource::RealTimeFor()` / `PerformanceTimeFor()` call `debugger()` in the CALLER's team** when the
  published drift is not exactly 1.0 and the time asked about is more than 2^24 µs (~16.8 s) from the last
  published pair. A time source that stops updating (e.g. a node whose device stream died) therefore takes down
  its consumers about 17 s later — typically the system mixer, freezing all audio. A node can avoid it by publishing
  a drift of exactly 1.0 (the large-difference path is only taken then). *(seen 2026-09-30; haiku `0dc37ccfb5`,
  `TimeSource.cpp:202-207`, `1 << FLT_MANT_DIG`)*
- **`BMidiLocalProducer`/`Consumer` and `BMidiEndpoint` rules are in §4.**

---

## 13. Debugging, diagnostics & working on a Haiku box remotely

- **On the crash dialog, choose *Save report*, not *Write core file*.** A live report symbolizes fully; a report
  regenerated from a core fails symbol lookup on every image yet still writes a `.report` file, so it looks like it
  worked. **Grep the report for `Exception`** to find the faulting thread — never assume it is the first one listed
  (`state: Debugged` only means *being debugged*). `Debugger --cli --core <file> -s` exists for remote use.
  *(Media-OS, 2026-08-06, hrev59922)*
- **Heap bugs:** `MALLOC_DEBUG=g LD_PRELOAD=/boot/system/lib/libroot_debug.so ./prog` (guarded heap) found a
  use-after-free that plain `.report` stacks garbled; the paranoid `p` mode **masked** it.
- **`setsid` does not exist on Haiku**, and there is no `reboot` binary. `(setsid sh -c "…" &)` silently does nothing.
  Use `nohup`: `sync; sync; nohup sh -c "sleep 6; shutdown -r" >/dev/null 2>&1 &` — the delay also lets the ssh
  session close first (an ssh socket torn down mid-shutdown triggered a stock TCP
  `PANIC: bound endpoint … not in hash!`, once). **Confirm the reboot happened** (`uptime`).
  *(2026-08-11, hrev59866+53)*
- **Killing an `ssh <haiku-box> '<job>'` (timeout, Ctrl-C) does NOT kill the job** — Haiku's sshd leaves the remote
  process running. Run anything long under `nohup … &`, poll a done-marker file, and `ps | grep` before relaunching,
  or two copies will run at once and look like a regression. *(2026-10-15)*
- *(observed)* **Recently written file contents can be lost across a reboot** — even one reported as clean — while
  metadata (name, size) survives; contents read back as stale blocks from other files. Seen repeatedly on one
  machine with unreliable power; root cause not pinned (BFS journals metadata, not data). After any install:
  `sync`, then verify a **content marker** (`strings <bin> | grep <new-string>`, or `objdump -d` at the changed
  call site), not md5/mtime — and re-verify after the reboot before interpreting any test. A hard hang has also
  corrupted a git index there: check `git log -1` actually moved after a reset. *(2026-07 → 2026-09-24)*
- **Verify the built artifact, not the source.** A guard comparing two source files proves nothing about the
  binary — a version header changed while a hand-rolled Makefile dependency list missed it, and the shipped server
  announced the previous version. `strings` the artifact. makefile-engine emits real `.d` files and can't miss a
  header. *(Media-OS `dc3d34f`, 2026-08-30)*
- **Validate any check against a known positive *and* a known negative.** A zero that matches nothing looks exactly
  like a pass (see the `find_thread` count in §11, and "no serial traffic" — an idle box emits nothing).
- **Instrumentation is part of the experiment.** Logging from an isochronous completion callback is **audible** and
  perturbs scheduling enough to fabricate the very errors being chased. Keep counters unconditional, put logging on
  its own trace bit (never the error bit), and treat an unnaturally constant value as a tell. For audio-quality
  questions use a synthesized tone, not music. *(Media-OS `c329b7e`, 2026-08-15)*
- **Serial KDL console:** KDL's line terminator is **CR**, not LF, and it polls the UART — send input **one
  character at a time** (~0.15 s apart) or only the last character arrives, which reads as a bare Enter with no
  error. Long output is **paged**: while paging, every character is a keypress (`q` exits). A floating RX line
  (cable unplugged at one end) floods KDL with junk input. `serialmark`-style markers written with
  `_kern_debug_output()` sit in the kernel line buffer until a newline — end them with `\n`.
  `_kern_kernel_debugger()` forces KDL from userland but only from a **foreground** process.
- **Entering KDL from the keyboard:** SysRq+D with the keymap's modifier — **Alt** on the BeOS-style layout,
  **Ctrl** on the PC-style one (Keymap preferences). For a hang: `threads 1` lists kernel threads with what each
  waits on (`mutex 0x…`); `mutex <addr>` names the lock and its waiters, and shows the **holder only on a
  KDEBUG-2 kernel** (master); an r1beta6 kernel (KDEBUG 1) prints no holder. `bt <id>` on each waiter.
  *(2026-10-03 / 2026-10-16)*
- **The syslog persists across reboots and rotates into `syslog.1`**, sometimes in the middle of a boot. Bound
  every check at the start of the boot in question: the last `Haiku revision` line (it appears twice per boot —
  boot loader, then kernel; take the first of the pair), reading back into `syslog.1` if it rotated.
- **`listimage 1`** lists every loaded kernel image **with its path** — the quickest proof of which copy of a
  driver or bus module (packaged or `non-packaged`) is actually running.
- ⚠️ **A shallow or stale Haiku source checkout lies about history.** In a shallow clone, `git log -- <path>` for any
  file older than the boundary silently reports the boundary commit as its last change. Check
  `git rev-list --count HEAD` and the branch before citing it. **The GitHub mirror `haiku/haiku` carries no `hrev`
  tags** — use `https://review.haiku-os.org/haiku`. **For "is this fixed upstream?", query live upstream**
  (`gh api "repos/haiku/haiku/commits?sha=master&path=<dir>"`), never a local checkout. *(2026-09-07 / 2026-09-29)*
- **hrev from a commit:** Haiku runs `git describe --dirty --tags --match=hrev* --abbrev=1`, strips `-g<hash>` and
  turns `-` into `+` (`build/scripts/determine_haiku_revision`): `hrev59866-79-g874f0` → `hrev59866+79`.

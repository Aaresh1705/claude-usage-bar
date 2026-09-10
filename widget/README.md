# Claude Usage Widget (Windows 11 widgets board)

A real Windows 11 widget — the supported kind: a packaged Win32 app that
implements `IWidgetProvider` from the Windows App SDK and hands the widget host
an Adaptive Card. It shows up in the Win+W board next to Weather, Sports and the
rest, and it reads the same OAuth token and the same endpoint as the tray app
(`~/.claude/.credentials.json` → `api.anthropic.com/api/oauth/usage`).

```
Claude usage                       updated 14:07
Session (5h)                                 86%
████████████████░░
resets 16:20 · in 1h 12m
Weekly                                       41%
███████░░░░░░░░░░░
resets Mon 09:00 · in 3d 4h
                          [ Refresh ] [ Usage page ]
```

* **small** — the busiest limit only, big number.
* **medium / large** — every limit the API reports, one bar each.
* Refreshes every 30 s while the board is on screen, every 5 min otherwise, and
  immediately when you click **Refresh**. One poll at a time.
* Log: `%LOCALAPPDATA%\claude-usage-bar\widget.log`.

## What this is *not*

It cannot replace the weather readout on the taskbar. That readout is Microsoft's
own widget/announcement surface and there is no API for it — third-party widgets
live inside the board. Anything you have seen sitting *on* the taskbar strip
(now-playing widgets and the like) is a borderless window drawn over the taskbar,
not a widget; deskbands, the old supported way, were removed in Windows 11.

## Build and install

Needs the .NET 8 SDK and **Developer Mode** (Settings → System → For developers).
Developer Mode is what lets Windows register an unsigned package straight from
the build folder; it is a one-time toggle and needs an administrator.

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

That publishes the provider into `package\ClaudeUsageWidget\`, regenerates the
icons and picker screenshot, and registers the package. Then open the board
(Win+W) → **Add widgets** → **Claude usage** → pin it.

Remove it with `build.ps1 -Unregister`.

No .NET SDK? Install one without administrator rights:

```powershell
curl -sSL -o dotnet-install.ps1 https://dot.net/v1/dotnet-install.ps1
.\dotnet-install.ps1 -Channel 8.0 -InstallDir "$HOME\.dotnet" -NoPath
```

`build.ps1` finds that per-user copy automatically.

### Without Developer Mode

If your machine's policy forbids Developer Mode, sign the package instead — the
Windows SDK tools come from NuGet, no Visual Studio needed:

```powershell
# makeappx/signtool land in ~\.nuget\packages\microsoft.windows.sdk.buildtools\...\x64\
makeappx pack /d package /p ClaudeUsageWidget.msix /o
$cert = New-SelfSignedCertificate -Type Custom -Subject "CN=Claude Usage Bar" `
        -KeyUsage DigitalSignature -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
signtool sign /fd SHA256 /sha1 $cert.Thumbprint ClaudeUsageWidget.msix
# elevated, once: trust the certificate, then install
Import-Certificate -FilePath cert.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople
Add-AppxPackage ClaudeUsageWidget.msix
```

The `Subject` must match the `Publisher` in `package\AppxManifest.xml`.

## Layout

| Path | |
| --- | --- |
| `ClaudeUsageWidget\Program.cs` | Registers the COM class object, waits until the last widget is unpinned. |
| `ClaudeUsageWidget\WidgetProvider.cs` | `IWidgetProvider`: pin/unpin, activate, actions, the refresh timer. |
| `ClaudeUsageWidget\Cards.cs` | Builds the Adaptive Card (bars are text, coloured by threshold). |
| `ClaudeUsageWidget\UsageClient.cs` | Token + usage endpoint, same parsing as the tray app. |
| `ClaudeUsageWidget\FactoryHelper.cs` | `IClassFactory` boilerplate from the Windows App SDK sample. |
| `package\AppxManifest.xml` | Package identity, the `windows.comServer` and widget extensions. |
| `make_assets.py` | Generates the logos, widget icon and picker screenshot with Pillow. |
| `build.ps1` | Publish → assemble → register. |

The CLSID `7ec0070b-4b01-4f2f-aeca-9eaa2b9aabf5` appears in `Program.cs` and twice
in `AppxManifest.xml`; all three must stay in sync.

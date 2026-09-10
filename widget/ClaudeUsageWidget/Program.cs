// Registers the widget provider's class object with OLE and then waits. The
// widget host launches this executable through the ComServer extension; it
// exits once the last widget is unpinned.

using System.Runtime.InteropServices;
using ClaudeUsageWidget;
using COM;

[DllImport("ole32.dll")]
static extern int CoRegisterClassObject(
    [MarshalAs(UnmanagedType.LPStruct)] Guid rclsid,
    [MarshalAs(UnmanagedType.IUnknown)] object pUnk,
    uint dwClsContext,
    uint flags,
    out uint lpdwRegister);

[DllImport("ole32.dll")]
static extern int CoRevokeClassObject(uint dwRegister);

const uint CLSCTX_LOCAL_SERVER = 0x4;
const uint REGCLS_MULTIPLEUSE = 0x1;

// Must match the com:Class Id and the CreateInstance ClassId in AppxManifest.xml.
var clsid = Guid.Parse("7ec0070b-4b01-4f2f-aeca-9eaa2b9aabf5");

var hr = CoRegisterClassObject(
    clsid,
    new WidgetProviderFactory<WidgetProvider>(),
    CLSCTX_LOCAL_SERVER,
    REGCLS_MULTIPLEUSE,
    out var cookie);

if (hr < 0)
{
    Log.Write($"CoRegisterClassObject failed: 0x{hr:X8}");
    Marshal.ThrowExceptionForHR(hr);
}

Log.Write("class object registered; waiting for the widget host");

using (var emptyWidgetListEvent = WidgetProvider.GetEmptyWidgetListEvent())
{
    emptyWidgetListEvent.WaitOne();
}

CoRevokeClassObject(cookie);
Log.Write("last widget unpinned; exiting");

// The provider runs as a background COM server with no console, so the log is
// the only way to see what it did. Same idea as the tray app's log.

namespace ClaudeUsageWidget;

internal static class Log
{
    private static readonly object Gate = new();

    private static readonly string Path = System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "claude-usage-bar", "widget.log");

    public static void Write(string message)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
                File.AppendAllText(Path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss}  {message}{Environment.NewLine}");
            }
        }
        catch
        {
            // Logging must never take the provider down.
        }
    }
}

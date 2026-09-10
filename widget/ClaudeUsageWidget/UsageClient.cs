// Reads the same OAuth token the tray app uses and calls the same endpoint
// Claude Code's own /usage command does. Nothing else leaves the machine.

using System.Net.Http.Headers;
using System.Text.Json;

namespace ClaudeUsageWidget;

internal record Limit(string Key, string Label, double Percent, DateTimeOffset? ResetsAt);

internal record UsageSnapshot(IReadOnlyList<Limit> Limits, string? Error, DateTimeOffset Updated)
{
    public static UsageSnapshot Failed(string error) =>
        new(Array.Empty<Limit>(), error, DateTimeOffset.Now);

    public Limit? ByKey(string key) =>
        Limits.FirstOrDefault(l => l.Key == key)
        ?? (key == "max" ? Limits.OrderByDescending(l => l.Percent).FirstOrDefault() : null);
}

internal static class UsageClient
{
    private const string UsageUrl = "https://api.anthropic.com/api/oauth/usage";

    private static readonly string CredentialsPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude", ".credentials.json");

    private static readonly Dictionary<string, string> Labels = new()
    {
        ["session"] = "Session (5h)",
        ["five_hour"] = "Session (5h)",
        ["weekly_all"] = "Weekly",
        ["seven_day"] = "Weekly",
        ["weekly_scoped"] = "Weekly (model)",
        ["seven_day_opus"] = "Weekly (Opus)",
    };

    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(20) };

    private static string? ReadToken()
    {
        using var stream = File.OpenRead(CredentialsPath);
        using var doc = JsonDocument.Parse(stream);
        foreach (var section in new[] { "claudeAiOauth", "claudeAiOAuth" })
        {
            if (doc.RootElement.TryGetProperty(section, out var blob) &&
                blob.TryGetProperty("accessToken", out var token))
            {
                return token.GetString();
            }
        }
        return null;
    }

    public static async Task<UsageSnapshot> FetchAsync()
    {
        string? token;
        try
        {
            token = ReadToken();
        }
        catch (FileNotFoundException)
        {
            return UsageSnapshot.Failed("Not signed in to Claude Code");
        }
        catch (Exception ex)
        {
            Log.Write($"credentials unreadable: {ex.Message}");
            return UsageSnapshot.Failed("Cannot read credentials");
        }

        if (string.IsNullOrEmpty(token))
        {
            return UsageSnapshot.Failed("Not signed in to Claude Code");
        }

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, UsageUrl);
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            request.Headers.Add("anthropic-beta", "oauth-2025-04-20");
            request.Headers.UserAgent.ParseAdd("claude-usage-widget/1.0");

            using var response = await Http.SendAsync(request).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                Log.Write($"usage http {(int)response.StatusCode}");
                return UsageSnapshot.Failed((int)response.StatusCode switch
                {
                    401 or 403 => "Session expired - run any Claude Code command",
                    429 => "Rate limited by the usage API",
                    _ => $"Usage API returned HTTP {(int)response.StatusCode}",
                });
            }

            using var body = await response.Content.ReadAsStreamAsync().ConfigureAwait(false);
            using var doc = await JsonDocument.ParseAsync(body).ConfigureAwait(false);
            return Parse(doc.RootElement);
        }
        catch (Exception ex)
        {
            Log.Write($"fetch failed: {ex}");
            return UsageSnapshot.Failed("Offline");
        }
    }

    private static UsageSnapshot Parse(JsonElement root)
    {
        var limits = new List<Limit>();

        if (root.TryGetProperty("limits", out var array) && array.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in array.EnumerateArray())
            {
                var kind = item.TryGetProperty("kind", out var k) ? k.GetString() ?? "unknown" : "unknown";
                var label = Labels.TryGetValue(kind, out var known)
                    ? known
                    : kind.Replace('_', ' ');

                if (item.TryGetProperty("scope", out var scope) &&
                    scope.TryGetProperty("model", out var model) &&
                    model.TryGetProperty("display_name", out var display))
                {
                    label = $"Weekly ({display.GetString()})";
                }

                limits.Add(new Limit(kind, label, Percent(item, "percent"), Reset(item, "resets_at")));
            }
        }

        // Older shape, kept as a fallback exactly like the tray app does.
        if (limits.Count == 0)
        {
            foreach (var (key, source) in new[] { ("session", "five_hour"), ("weekly_all", "seven_day") })
            {
                if (root.TryGetProperty(source, out var blob) &&
                    blob.TryGetProperty("utilization", out _))
                {
                    limits.Add(new Limit(key, Labels[key], Percent(blob, "utilization"), Reset(blob, "resets_at")));
                }
            }
        }

        return limits.Count == 0
            ? UsageSnapshot.Failed("No limits reported")
            : new UsageSnapshot(limits, null, DateTimeOffset.Now);
    }

    private static double Percent(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.GetDouble()
            : 0;

    private static DateTimeOffset? Reset(JsonElement element, string name) =>
        element.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.String &&
        DateTimeOffset.TryParse(value.GetString(), out var parsed)
            ? parsed.ToLocalTime()
            : null;
}

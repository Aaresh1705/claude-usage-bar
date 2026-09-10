// Adaptive Card JSON for the widget board. Built in code rather than as a
// template + data pair: the whole card is small and it keeps the bar drawing
// (which is just text) in one place.

using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace ClaudeUsageWidget;

internal static class Cards
{
    private const string UsagePage = "https://claude.ai/settings/usage";
    private const int BarCells = 18;

    public static string Build(UsageSnapshot usage, string widgetSize)
    {
        var body = new JsonArray
        {
            new JsonObject
            {
                ["type"] = "ColumnSet",
                ["columns"] = new JsonArray
                {
                    Column("stretch", new JsonArray
                    {
                        Text("Claude usage", size: "Small", weight: "Bolder", isSubtle: true),
                    }),
                    Column("auto", new JsonArray
                    {
                        Text(usage.Error is null ? $"updated {usage.Updated:HH:mm}" : "unavailable",
                             size: "Small", isSubtle: true),
                    }),
                },
            },
        };

        if (usage.Error is not null)
        {
            body.Add(Text(usage.Error, wrap: true, color: "Attention"));
        }
        else
        {
            var limits = Visible(usage, widgetSize);
            foreach (var limit in limits)
            {
                foreach (var element in Row(limit, compact: widgetSize == "small"))
                {
                    body.Add(element);
                }
            }
        }

        var card = new JsonObject
        {
            ["type"] = "AdaptiveCard",
            ["$schema"] = "http://adaptivecards.io/schemas/adaptive-card.json",
            ["version"] = "1.5",
            ["body"] = body,
            ["actions"] = new JsonArray
            {
                new JsonObject
                {
                    ["type"] = "Action.Execute",
                    ["title"] = "Refresh",
                    ["verb"] = "refresh",
                },
                new JsonObject
                {
                    ["type"] = "Action.OpenUrl",
                    ["title"] = "Usage page",
                    ["url"] = UsagePage,
                },
            },
        };

        return card.ToJsonString(new JsonSerializerOptions { WriteIndented = false });
    }

    /// <summary>Small shows only the busiest limit; larger sizes show them all.</summary>
    private static IEnumerable<Limit> Visible(UsageSnapshot usage, string widgetSize) =>
        widgetSize == "small"
            ? usage.Limits.OrderByDescending(l => l.Percent).Take(1)
            : usage.Limits;

    private static IEnumerable<JsonNode> Row(Limit limit, bool compact)
    {
        var pct = Math.Clamp(limit.Percent, 0, 100);
        var spent = pct >= 99.5;

        yield return new JsonObject
        {
            ["type"] = "ColumnSet",
            ["spacing"] = "Small",
            ["columns"] = new JsonArray
            {
                Column("stretch", new JsonArray { Text(limit.Label, weight: "Bolder", wrap: true) }),
                Column("auto", new JsonArray
                {
                    Text(spent ? "SPENT" : $"{Math.Round(pct)}%",
                         weight: "Bolder",
                         size: compact ? "ExtraLarge" : "Default",
                         color: ColorFor(pct)),
                }),
            },
        };

        yield return Text(Bar(pct), fontType: "Monospace", color: ColorFor(pct), spacing: "None");

        var reset = limit.ResetsAt is null ? null : Countdown(limit.ResetsAt.Value);
        if (reset is not null)
        {
            yield return Text(reset, size: "Small", isSubtle: true, spacing: "None");
        }
    }

    private static string Bar(double pct)
    {
        var filled = (int)Math.Round(pct / 100.0 * BarCells);
        var sb = new StringBuilder(BarCells);
        sb.Append('█', Math.Clamp(filled, 0, BarCells));
        sb.Append('░', BarCells - Math.Clamp(filled, 0, BarCells));
        return sb.ToString();
    }

    private static string ColorFor(double pct) => pct switch
    {
        >= 85 => "Attention",
        >= 60 => "Warning",
        _ => "Good",
    };

    private static string Countdown(DateTimeOffset resetsAt)
    {
        var delta = resetsAt - DateTimeOffset.Now;
        if (delta <= TimeSpan.Zero)
        {
            return "resetting now";
        }

        var human = delta.TotalHours >= 24
            ? $"{(int)delta.TotalDays}d {delta.Hours}h"
            : delta.TotalHours >= 1
                ? $"{(int)delta.TotalHours}h {delta.Minutes}m"
                : $"{delta.Minutes}m";

        var when = delta.TotalHours >= 24 ? resetsAt.ToString("ddd HH:mm") : resetsAt.ToString("HH:mm");
        return $"resets {when} · in {human}";
    }

    private static JsonObject Column(string width, JsonArray items) => new()
    {
        ["type"] = "Column",
        ["width"] = width,
        ["verticalContentAlignment"] = "Center",
        ["items"] = items,
    };

    private static JsonObject Text(
        string text,
        string? size = null,
        string? weight = null,
        string? color = null,
        string? fontType = null,
        string? spacing = null,
        bool isSubtle = false,
        bool wrap = false)
    {
        var node = new JsonObject
        {
            ["type"] = "TextBlock",
            ["text"] = text,
        };
        if (size is not null) node["size"] = size;
        if (weight is not null) node["weight"] = weight;
        if (color is not null) node["color"] = color;
        if (fontType is not null) node["fontType"] = fontType;
        if (spacing is not null) node["spacing"] = spacing;
        if (isSubtle) node["isSubtle"] = true;
        if (wrap) node["wrap"] = true;
        return node;
    }
}

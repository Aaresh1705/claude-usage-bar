// The IWidgetProvider implementation. The widget host COM-activates this class
// (see the ComServer extension in AppxManifest.xml) and calls these methods.

using System.Collections.Concurrent;
using Microsoft.Windows.Widgets.Providers;

namespace ClaudeUsageWidget;

/// <summary>What we need to remember about one pinned widget.</summary>
internal sealed class PinnedWidget
{
    public required string Id { get; init; }
    public required string DefinitionId { get; init; }
    public string Size { get; set; } = "medium";
    public bool IsActive { get; set; }
}

internal class WidgetProvider : IWidgetProvider
{
    private static readonly ManualResetEvent EmptyWidgetListEvent = new(false);
    private static readonly ConcurrentDictionary<string, PinnedWidget> Pinned = new();

    private static readonly TimeSpan ActiveInterval = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan IdleInterval = TimeSpan.FromMinutes(5);

    private static UsageSnapshot _usage = UsageSnapshot.Failed("Loading…");
    private static DateTimeOffset _fetchedAt = DateTimeOffset.MinValue;
    private static readonly SemaphoreSlim FetchGate = new(1, 1);
    private static Timer? _timer;

    public WidgetProvider()
    {
        // A restart of the provider (reboot, crash, host recycling) must not
        // lose track of widgets that are still pinned on the board.
        foreach (var info in WidgetManager.GetDefault().GetWidgetInfos())
        {
            var context = info.WidgetContext;
            Pinned[context.Id] = new PinnedWidget
            {
                Id = context.Id,
                DefinitionId = context.DefinitionId,
                Size = context.Size.ToString().ToLowerInvariant(),
            };
        }

        Log.Write($"provider started, {Pinned.Count} widget(s) already pinned");
        _timer ??= new Timer(_ => Tick(), null, TimeSpan.Zero, ActiveInterval);
    }

    public static ManualResetEvent GetEmptyWidgetListEvent() => EmptyWidgetListEvent;

    // -- IWidgetProvider ----------------------------------------------------

    public void CreateWidget(WidgetContext widgetContext)
    {
        var widget = new PinnedWidget
        {
            Id = widgetContext.Id,
            DefinitionId = widgetContext.DefinitionId,
            Size = widgetContext.Size.ToString().ToLowerInvariant(),
        };
        Pinned[widget.Id] = widget;
        Log.Write($"widget pinned: {widget.DefinitionId} ({widget.Size})");
        Refresh(force: true);
    }

    public void DeleteWidget(string widgetId, string customState)
    {
        Pinned.TryRemove(widgetId, out _);
        Log.Write($"widget unpinned, {Pinned.Count} left");
        if (Pinned.IsEmpty)
        {
            EmptyWidgetListEvent.Set();
        }
    }

    public void OnActionInvoked(WidgetActionInvokedArgs actionInvokedArgs)
    {
        if (actionInvokedArgs.Verb == "refresh")
        {
            Refresh(force: true);
        }
    }

    public void OnWidgetContextChanged(WidgetContextChangedArgs contextChangedArgs)
    {
        var context = contextChangedArgs.WidgetContext;
        if (Pinned.TryGetValue(context.Id, out var widget))
        {
            widget.Size = context.Size.ToString().ToLowerInvariant();
            Push(widget);
        }
    }

    public void Activate(WidgetContext widgetContext)
    {
        if (Pinned.TryGetValue(widgetContext.Id, out var widget))
        {
            widget.IsActive = true;
            widget.Size = widgetContext.Size.ToString().ToLowerInvariant();
        }
        // The board is on screen: show what we have immediately, then freshen it.
        Push();
        Refresh(force: false);
    }

    public void Deactivate(string widgetId)
    {
        if (Pinned.TryGetValue(widgetId, out var widget))
        {
            widget.IsActive = false;
        }
    }

    // -- data ---------------------------------------------------------------

    private static void Tick()
    {
        if (Pinned.IsEmpty)
        {
            return;
        }

        var interval = Pinned.Values.Any(w => w.IsActive) ? ActiveInterval : IdleInterval;
        if (DateTimeOffset.Now - _fetchedAt >= interval)
        {
            Refresh(force: false);
        }
        else
        {
            Push();   // keeps the "resets in" countdown honest between fetches
        }
    }

    private static void Refresh(bool force)
    {
        _ = Task.Run(async () =>
        {
            // One poll at a time; a burst of Activate calls must not fan out
            // into a burst of requests against the usage endpoint.
            if (!await FetchGate.WaitAsync(0).ConfigureAwait(false))
            {
                return;
            }

            try
            {
                if (!force && DateTimeOffset.Now - _fetchedAt < TimeSpan.FromSeconds(15))
                {
                    Push();
                    return;
                }

                _usage = await UsageClient.FetchAsync().ConfigureAwait(false);
                _fetchedAt = DateTimeOffset.Now;
                Push();
            }
            catch (Exception ex)
            {
                Log.Write($"refresh failed: {ex}");
            }
            finally
            {
                FetchGate.Release();
            }
        });
    }

    private static void Push()
    {
        foreach (var widget in Pinned.Values)
        {
            Push(widget);
        }
    }

    private static void Push(PinnedWidget widget)
    {
        try
        {
            var options = new WidgetUpdateRequestOptions(widget.Id)
            {
                Template = Cards.Build(_usage, widget.Size),
                Data = "{}",
                CustomState = widget.Size,
            };
            WidgetManager.GetDefault().UpdateWidget(options);
        }
        catch (Exception ex)
        {
            Log.Write($"update {widget.Id} failed: {ex.Message}");
        }
    }
}

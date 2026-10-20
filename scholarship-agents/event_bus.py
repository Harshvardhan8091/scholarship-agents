import asyncio
import inspect
import sys
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, Union
from rich.console import Console
from rich.theme import Theme

# Ensure utf-8 encoding on Windows terminal
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from models import PipelineEvent

custom_theme = Theme({
    "started": "bold cyan",
    "progress": "bright_blue",
    "recovery": "bold yellow",
    "done": "bold green",
    "error": "bold red",
    "agent": "bold magenta",
    "time": "dim white",
})

console = Console(theme=custom_theme, force_terminal=True, legacy_windows=False)

EventHandler = Union[
    Callable[[PipelineEvent], Any],
    Callable[[PipelineEvent], Coroutine[Any, Any, Any]],
]


class EventBus:
    def __init__(self, print_to_terminal: bool = True):
        self._listeners: List[EventHandler] = []
        self._history: List[PipelineEvent] = []
        self._print_to_terminal = print_to_terminal
        self._queues: List[asyncio.Queue[PipelineEvent]] = []

    def subscribe(self, handler: EventHandler) -> None:
        if handler not in self._listeners:
            self._listeners.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        if handler in self._listeners:
            self._listeners.remove(handler)

    def create_stream_queue(self) -> asyncio.Queue[PipelineEvent]:
        q: asyncio.Queue[PipelineEvent] = asyncio.Queue()
        self._queues.append(q)
        return q

    def remove_stream_queue(self, q: asyncio.Queue[PipelineEvent]) -> None:
        if q in self._queues:
            self._queues.remove(q)

    async def emit(
        self,
        agent: str,
        event_type: Literal["started", "progress", "recovery", "done", "error"],
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> PipelineEvent:
        event = PipelineEvent(
            agent=agent,
            type=event_type,
            message=message,
            details=details,
        )
        self._history.append(event)

        if self._print_to_terminal:
            self._print_event(event)

        # Notify queues (e.g. for SSE)
        for q in list(self._queues):
            await q.put(event)

        # Notify registered listener callbacks
        for listener in list(self._listeners):
            try:
                res = listener(event)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                console.print(f"[bold red]Listener Error:[/bold red] {e}")

        return event

    def _print_event(self, event: PipelineEvent) -> None:
        time_part = f"[{event.timestamp[11:19]}]"
        agent_part = f"[{event.agent}]"

        icon = {
            "started": "🚀",
            "progress": "⚙️",
            "recovery": "🔁 [RECOVERY]",
            "done": "✅",
            "error": "❌ [ERROR]",
        }.get(event.type, "ℹ️")

        type_style = event.type
        console.print(
            f"[time]{time_part}[/time] {icon} [agent]{agent_part}[/agent] "
            f"[{type_style}]{event.message}[/{type_style}]"
        )

    def get_history(self) -> List[PipelineEvent]:
        return list(self._history)

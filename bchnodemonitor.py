import os
import subprocess
import json
import time
import threading
from datetime import datetime
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

# --- Configuration ---
APP_VERSION = "V1.1.3"
BCH_BIN_PATH = "bitcoin-cli" 
PROCESS_NAME = "bitcoind"
REFRESH_RATE = 2
MAX_ROWS_PER_TABLE = 20
BCH_DATA_DIR = os.path.expanduser("~/.bitcoin")

class SystemMonitor:
    @staticmethod
    def get_global_cpu():
        try:
            with open('/proc/stat', 'r') as f:
                fields = [float(column) for column in f.readline().strip().split()[1:]]
            return sum(fields), fields[3] # Total, Idle
        except: return 0, 0

    @staticmethod
    def get_node_stats():
        try:
            pid = subprocess.check_output(["pgrep", "-x", PROCESS_NAME]).decode().strip()
            stat = subprocess.check_output(["ps", "-p", pid, "-o", "%cpu,rss"]).decode().splitlines()[1].split()
            cpu_usage = float(stat[0])
            ram_gb = float(stat[1]) / (1024 * 1024)
            return cpu_usage, ram_gb
        except: return 0.0, 0.0

    @staticmethod
    def get_system_ram():
        try:
            mem = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    parts = line.split()
                    mem[parts[0].rstrip(':')] = int(parts[1])
            used = (mem['MemTotal'] - mem.get('MemAvailable', mem['MemFree'])) / (1024 * 1024)
            return used
        except: return 0.0

    @staticmethod
    def get_dir_size(path):
        total = 0
        try:
            if not os.path.exists(path): return 0
            for entry in os.scandir(path):
                if entry.is_file(): total += entry.stat().st_size
                elif entry.is_dir(): total += SystemMonitor.get_dir_size(entry.path)
        except: pass
        return total / (1024**3)

class BCHNodeMonitor:
    def __init__(self):
        self.sys_mon = SystemMonitor()
        self.start_time = time.time()
        self.last_cpu_total, self.last_cpu_idle = self.sys_mon.get_global_cpu()
        self.data = {
            "node": {}, "system": {}, "blockchain": {},
            "peers_in": [], "peers_out": [],
            "last_update": "---"
        }
        self.lock = threading.Lock()

    def _run_cli(self, cmd_args):
        try:
            result = subprocess.run([BCH_BIN_PATH] + cmd_args, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except: return None

    def update_data(self):
        t2, i2 = self.sys_mon.get_global_cpu()
        diff_total = t2 - self.last_cpu_total
        diff_idle = i2 - self.last_cpu_idle
        sys_cpu = (1 - (diff_idle / diff_total)) * 100 if diff_total > 0 else 0
        self.last_cpu_total, self.last_cpu_idle = t2, i2

        node_cpu, node_ram = self.sys_mon.get_node_stats()
        sys_ram = self.sys_mon.get_system_ram()

        bc_info = self._run_cli(["getblockchaininfo"])
        net_info = self._run_cli(["getnetworkinfo"])
        mining_info = self._run_cli(["getmininginfo"])
        uptime_info = self._run_cli(["uptime"])
        peers = self._run_cli(["getpeerinfo"]) or []
        
        blocks_size = self.sys_mon.get_dir_size(os.path.join(BCH_DATA_DIR, "blocks"))
        chain_size = self.sys_mon.get_dir_size(os.path.join(BCH_DATA_DIR, "chainstate"))
        
        with self.lock:
            self.data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if bc_info:
                self.data["node"] = {"ver": net_info.get("subversion", "N/A") if net_info else "N/A"}
                node_uptime = uptime_info if isinstance(uptime_info, int) else (time.time() - self.start_time)
                self.data["blockchain"] = {
                    "height": bc_info.get("blocks", 0),
                    "sync": bc_info.get("verificationprogress", 0) * 100,
                    "difficulty": self.format_diff(bc_info.get("difficulty", 0)),
                    "hashrate": (mining_info.get("networkhashps", 0) if mining_info else 0) / 1e18,
                    "uptime": self.format_uptime(node_uptime)
                }
            self.data["system"] = {
                "sys_cpu": sys_cpu, "node_cpu": node_cpu,
                "sys_ram": sys_ram, "node_ram": node_ram,
                "blocks_gb": blocks_size, "chain_gb": chain_size
            }
            sorted_peers = sorted(peers, key=lambda x: x.get('pingtime', 999))
            self.data["peers_in"] = [p for p in sorted_peers if p.get("inbound")]
            self.data["peers_out"] = [p for p in sorted_peers if not p.get("inbound")]

    def format_uptime(self, seconds):
        days, rem = divmod(int(seconds), 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        return f"{days}d {hours}h {mins}m" if days > 0 else f"{hours}h {mins}m {secs}s"

    def format_diff(self, val):
        if val >= 1e12: return f"{val/1e12:.2f} T"
        if val >= 1e9: return f"{val/1e9:.2f} G"
        return f"{val:.2f}"

    def generate_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(Layout(name="top", size=6), Layout(name="mid", size=3), Layout(name="outbound", ratio=1), Layout(name="inbound", ratio=1))

        sys, bc, node = self.data["system"], self.data["blockchain"], self.data["node"]
        perf_grid = Table.grid(expand=True)
        perf_grid.add_column(ratio=1); perf_grid.add_column(ratio=1)
        
        n_info = (f"[bold bright_magenta]BCH Version:[/] {node.get('ver', '...')}\n"
                  f"[bold bright_magenta]CPU Usage:[/]   System: {sys.get('sys_cpu', 0):.1f}% | Node: {sys.get('node_cpu', 0):.1f}%\n"
                  f"[bold bright_magenta]RAM Usage:[/]   System: {sys.get('sys_ram', 0):.2f} GB | Node: {sys.get('node_ram', 0):.2f} GB\n"
                  f"[bold bright_magenta]Disk Space:[/]  Blocks: {sys.get('blocks_gb', 0):.2f} GB | Chainstate: {sys.get('chain_gb', 0):.2f} GB")
        
        b_info = (f"[bold bright_yellow]Height:[/]     {bc.get('height', 0)} (Synced: {bc.get('sync', 0):.2f}%)\n"
                  f"[bold bright_yellow]Difficulty:[/] {bc.get('difficulty', '0')}\n"
                  f"[bold bright_yellow]Net Hash:[/]   {bc.get('hashrate', 0):.2f} EH/s\n"
                  f"[bold bright_yellow]Uptime:[/]     {bc.get('uptime', '...')}")

        perf_grid.add_row(Panel(n_info, title="[bold bright_magenta]Node & System[/]", border_style="bright_magenta"), Panel(b_info, title="[bold bright_yellow]Blockchain Data[/]", border_style="bright_yellow"))
        layout["top"].update(perf_grid)
        
        ti, to = len(self.data["peers_in"]), len(self.data["peers_out"])
        status_text = Text.from_markup(f"Total Connected Peers: [bold white]{ti+to}[/]  |  Outbound: [bold bright_cyan]{to}[/]  |  Inbound: [bold bright_green]{ti}[/]  |  Last Updated: [bold bright_white]{self.data['last_update']}[/]")
        status_text.append(f"  |  App. Version: {APP_VERSION}", style="dim bright_white")
        layout["mid"].update(Panel(status_text, title="[bold bright_white]Network Status[/]", border_style="bright_white"))

        layout["outbound"].update(self.create_peer_tables(self.data["peers_out"], "Outbound Peers", "bright_cyan"))
        layout["inbound"].update(self.create_peer_tables(self.data["peers_in"], "Inbound Peers", "bright_green"))
        return layout

    def create_peer_tables(self, peer_list, title, border_color):
        if not peer_list: return Panel(Text("No peers connected", style="dim"), title=f"[bold]{title}[/]", border_style=border_color)
        final_row = Table.grid(padding=(0, 2))
        chunks = [peer_list[i:i + MAX_ROWS_PER_TABLE] for i in range(0, len(peer_list), MAX_ROWS_PER_TABLE)]
        renderable_tables = []
        for chunk_idx, chunk in enumerate(chunks):
            t = Table(show_header=True, header_style="bold bright_white", border_style="bright_black")
            t.add_column("No.", style="bright_white", justify="right"); t.add_column("IP Address:Port", style="bright_white"); t.add_column("Ping", justify="right")
            for i, p in enumerate(chunk):
                raw_ping = p.get('pingtime')
                ping_ms = round(raw_ping * 1000) if raw_ping else None
                style = "bright_green" if ping_ms and ping_ms <= 50 else "bright_yellow" if ping_ms and ping_ms <= 150 else "red"
                t.add_row(f"{(chunk_idx * MAX_ROWS_PER_TABLE) + i + 1}.", p.get("addr", "N/A"), f"[{style}]{ping_ms} ms[/]" if ping_ms else "[dim]---[/]")
            renderable_tables.append(t)
        final_row.add_row(*renderable_tables); return Panel(final_row, title=f"[bold]{title}[/]", border_style=border_color, expand=True)

def main():
    monitor = BCHNodeMonitor()
    threading.Thread(target=lambda: [monitor.update_data() or time.sleep(REFRESH_RATE) for _ in iter(int, 1)], daemon=True).start()
    with Live(monitor.generate_layout(), screen=True, refresh_per_second=4) as live:
        try:
            while True:
                live.update(monitor.generate_layout()); time.sleep(0.25)
        except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()

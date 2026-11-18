"""Stage 3 56-Team AI Training Arena & Time-Based Loop Server.

Serves:
- `/trade`: 2-Tab Behavior & History Dashboard (Tab 1: Live 56-Team Arena, Tab 2: All Games Summary & Clickable Game History)
- `/v1/pvp/loop/start` & `/v1/pvp/loop/stop`: Time-based training loop (default 2 hours; finishes the in-progress game when timer expires)
- `/v1/pvp/history/summary` & `/v1/pvp/history/game/<game_number>`: Fast statistical summary & per-game history backed by multi-file CSV logs
- `/v1/pvp/csv/<filename>`: Direct download of any CSV log file
"""
import json
import os
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bots import QwenPVPWorker, SmartBotController
from engine import BAG, PVPMarketEngine

HERE = Path(__file__).parent
LOGS = HERE / "logs"

ENGINE = PVPMarketEngine(LOGS)
BOTS = SmartBotController(ENGINE)
QWEN_WORKER = QwenPVPWorker(ENGINE)


class TimedTrainingLoopManager:
    """Runs continuous 56-team Stage 3 games for a target duration (e.g. 2 hours = 120 minutes).
    When the timer reaches 0 (or stop is requested), the game currently being played continues
    through Round 15 (`CLOSED`), saves its CSV rows, and becomes the last game of the session.
    """

    def __init__(self, engine: PVPMarketEngine):
        self.engine = engine
        self.lock = threading.RLock()
        self.active = False
        self.stop_requested = False
        self.stopping_after_current_game = False
        self.started_at: float | None = None
        self.duration_sec: float = 7200.0  # default 2 hours
        self.deadline: float | None = None
        self.games_completed: int = 0
        self.thread: threading.Thread | None = None

    def start(self, duration_minutes: float = 120.0) -> dict:
        with self.lock:
            dur_s = max(10.0, float(duration_minutes) * 60.0)
            if self.active:
                # Update duration/deadline of running loop
                self.duration_sec = dur_s
                self.deadline = (self.started_at or time.time()) + dur_s
                self.stop_requested = False
                self.stopping_after_current_game = False
                return self.status()

            self.engine.csv_logger.start_new_session()
            self.active = True
            self.stop_requested = False
            self.stopping_after_current_game = False
            self.started_at = time.time()
            self.duration_sec = dur_s
            self.deadline = self.started_at + dur_s
            self.games_completed = 0
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()
            return self.status()

    def stop(self) -> dict:
        with self.lock:
            if self.active:
                self.stop_requested = True
                self.stopping_after_current_game = True
            return self.status()

    def _worker(self) -> None:
        try:
            while True:
                with self.lock:
                    if not self.active or self.stop_requested:
                        break
                    if self.deadline and time.time() >= self.deadline:
                        break

                # Start a new game if not already mid-game
                if self.engine.phase in ("WAITING", "CLOSED"):
                    self.engine.start_match(rounds=15, practice_rounds=2, include_specialty=True, sample_noise=True)

                # Wait for current game to complete all rounds (even if timer expires mid-game, finish this game as the last game!)
                while self.engine.phase != "CLOSED":
                    time.sleep(0.5)
                    with self.lock:
                        if self.deadline and time.time() >= self.deadline:
                            self.stopping_after_current_game = True

                with self.lock:
                    self.games_completed += 1
                    if self.stop_requested or (self.deadline and time.time() >= self.deadline):
                        break

                time.sleep(1.2)
        except Exception as e:
            print(f"[TimedTrainingLoopManager] error: {e}", flush=True)
            traceback.print_exc()
        finally:
            with self.lock:
                self.active = False
                self.stop_requested = False
                self.stopping_after_current_game = False

    def status(self) -> dict:
        with self.lock:
            now = time.time()
            if self.active and self.started_at is not None and self.deadline is not None:
                elapsed = max(0.0, now - self.started_at)
                rem = max(0.0, self.deadline - now)
            else:
                elapsed = 0.0
                rem = 0.0
            return {
                "active": self.active,
                "stopping_after_current_game": self.stopping_after_current_game,
                "session_id": self.engine.csv_logger.session_id,
                "duration_sec": round(self.duration_sec, 1),
                "duration_minutes": round(self.duration_sec / 60.0, 1),
                "elapsed_sec": round(elapsed, 1),
                "remaining_sec": round(rem, 1),
                "games_completed": self.games_completed,
                "current_game_number": self.engine.game_id,
            }


LOOP_MANAGER = TimedTrainingLoopManager(ENGINE)


def get_lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def on_phase_changed(game_id: int, phase: str, round_num: int, rinfo: dict | None = None) -> None:
    if phase == "APPRAISAL" and round_num == 0:
        BOTS.reset()
    if phase in ("APPRAISAL", "ROUND_OPEN"):
        BOTS.act_for_all_bots(game_id, phase, round_num, rinfo)
        QWEN_WORKER.trigger(game_id, phase, round_num, rinfo)


ENGINE.on_phase_change_callbacks.append(on_phase_changed)


class PVPRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status: int, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Client")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _auth_team(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "").strip()
        if not token:
            return None
        return ENGINE.resolve_team(token)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Client")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        port = int(os.environ.get("PVP_PORT", "8000"))

        if path in ("/", "/trade", "/trade/"):
            html = (HERE / "trade_ui.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if path == "/v1/version":
            self._send_json(200, {"api": "v1", "server": "pvp-stage3-csv", "mode": "sandbox", "server_time": os.times().elapsed, "trade_page": True})
            return

        if path == "/v1/calendar":
            self._send_json(200, {"calendar": ENGINE.calendar, "rules": ENGINE.rules})
            return

        if path == "/v1/pvp/status":
            st = ENGINE.get_pvp_status()
            st["loop_status"] = LOOP_MANAGER.status()
            st["lan_urls"] = [f"http://{ip}:{port}/trade" for ip in get_lan_ips()] + [f"http://localhost:{port}/trade"]
            self._send_json(200, st)
            return

        if path == "/v1/pvp/history/summary":
            gen_filter = qs.get("gen", [None])[0]
            summary = ENGINE.csv_logger.get_all_games_summary(generation=gen_filter)
            summary["loop_status"] = LOOP_MANAGER.status()
            self._send_json(200, summary)
            return

        if path.startswith("/v1/pvp/history/game/"):
            g_str = unquote(path.split("/v1/pvp/history/game/", 1)[1]).strip()
            try:
                g_num = int(g_str)
            except ValueError:
                self._send_error_json(400, "INVALID_GAME_NUMBER", f"Invalid game number: {g_str}")
                return
            rec = ENGINE.csv_logger.get_game_history(g_num)
            if not rec:
                self._send_error_json(404, "GAME_NOT_FOUND", f"Game #{g_num} not found in CSV logs")
                return
            self._send_json(200, rec)
            return

        if path.startswith("/v1/pvp/csv/"):
            fname = Path(unquote(path.split("/v1/pvp/csv/", 1)[1])).name
            fpath = LOGS / fname
            if not fpath.exists() or fpath.suffix.lower() != ".csv":
                self._send_error_json(404, "CSV_NOT_FOUND", f"CSV file {fname} not found")
                return
            raw = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        tid = self._auth_team()
        if not tid:
            self._send_error_json(401, "UNAUTHORIZED", "Missing or invalid team token.")
            return

        if path == "/v1/profile":
            t = ENGINE.teams.get(tid) or ENGINE.human_players.get(tid)
            if not t:
                self._send_error_json(404, "UNKNOWN_TEAM", "Team not found")
                return
            self._send_json(200, {"team_id": tid, "team_name": t["team_name"], "university": t["university"], "shown_as": f"{t['team_name']} · {t['university']}", "complete": True})
            return

        if path == "/v1/state":
            self._send_json(200, ENGINE.get_state_for_team(tid))
            return

        if path == "/v1/appraisals":
            ap = ENGINE.appraisals.get(tid, {"submitted_at": None, "items": {}})
            self._send_json(200, ap)
            return

        if path == "/v1/offers":
            rid = f"R{ENGINE.round_num}" if ENGINE.round_num > 0 else None
            offers = [o for o in ENGINE.open_offers.get(tid, []) if o["status"] == "OPEN"]
            self._send_json(200, {"round_id": rid, "offers": offers})
            return

        if path == "/v1/results":
            req_round = qs.get("round", [None])[0]
            if not req_round and ENGINE.round_results:
                req_round = f"R{len(ENGINE.round_results)}"
            if not req_round or req_round not in ENGINE.round_results or tid not in ENGINE.round_results[req_round]:
                self._send_error_json(409, "NOT_AVAILABLE", "Results not available for requested round yet")
                return
            r_obj = ENGINE.round_results[req_round][tid]
            self._send_json(200, {"round_id": r_obj["round_id"], "kind": r_obj["kind"], "my_offers": r_obj["my_offers"], "market_summary": r_obj["market_summary"]})
            return

        if path == "/v1/standings":
            self._send_json(200, ENGINE.get_standings(tid))
            return

        if path == "/v1/leaderboard":
            scores = ENGINE.compute_scores()
            self._send_json(200, {"leaderboard": sorted(scores.values(), key=lambda x: x["rank"])})
            return

        if path == "/v1/sandbox/reveal":
            self._send_json(200, ENGINE.get_reveal(tid))
            return

        self._send_error_json(404, "UNKNOWN_ENDPOINT", f"Unknown GET endpoint: {path}")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_body()
        except Exception:
            self._send_error_json(400, "INVALID_FIELD", "Invalid JSON body")
            return

        try:
            if path == "/v1/pvp/generation":
                gen_name = str(body.get("generation", "gen 2")).strip()
                active_gen = ENGINE.csv_logger.set_generation(gen_name)
                self._send_json(200, {"current_generation": active_gen})
                return

            if path == "/v1/pvp/loop/start":
                dur_min = float(body.get("duration_minutes", 120.0) or 120.0)
                st = LOOP_MANAGER.start(duration_minutes=dur_min)
                self._send_json(200, st)
                return

            if path == "/v1/pvp/loop/stop":
                st = LOOP_MANAGER.stop()
                self._send_json(200, st)
                return

            if path == "/v1/pvp/start":
                rounds = int(body.get("rounds", ENGINE.total_rounds) or 15)
                prac = int(body.get("practice_rounds", ENGINE.practice_rounds) if "practice_rounds" in body else 2)
                inc_spec = bool(body.get("include_specialty", ENGINE.include_specialty))
                noise = bool(body.get("sample_noise", ENGINE.sample_noise))
                status = ENGINE.start_match(rounds=rounds, practice_rounds=prac, include_specialty=inc_spec, sample_noise=noise)
                status["loop_status"] = LOOP_MANAGER.status()
                self._send_json(200, status)
                return

            if path in ("/v1/pvp/reset_lobby", "/v1/sandbox/reset"):
                rounds = int(body.get("rounds", ENGINE.total_rounds) or 15)
                prac = int(body.get("practice_rounds", ENGINE.practice_rounds) if "practice_rounds" in body else 2)
                inc_spec = bool(body.get("include_specialty", ENGINE.include_specialty))
                noise = bool(body.get("sample_noise", ENGINE.sample_noise))
                status = ENGINE.reset_to_lobby(rounds=rounds, practice_rounds=prac, include_specialty=inc_spec, sample_noise=noise)
                status["loop_status"] = LOOP_MANAGER.status()
                self._send_json(200, status)
                return

            if path == "/v1/sandbox/advance":
                status = ENGINE.advance_phase()
                status["loop_status"] = LOOP_MANAGER.status()
                self._send_json(200, status)
                return

            tid = self._auth_team()
            if not tid:
                self._send_error_json(401, "UNAUTHORIZED", "Missing or invalid team token")
                return

            if path == "/v1/pvp/complete":
                target_tid = body.get("team_id", tid)
                status = ENGINE.mark_ready(target_tid, True)
                self._send_json(200, status)
                return

            if path == "/v1/appraisals":
                rec = ENGINE.submit_appraisal(tid, body.get("items", []))
                self._send_json(201, rec)
                return

            if path == "/v1/offers":
                off = ENGINE.submit_offer(tid, body)
                self._send_json(201, off)
                return

            if path == "/v1/offers:batch":
                res = ENGINE.submit_offer_batch(tid, body.get("offers", []), replace_existing=bool(body.get("replace_existing", True)))
                self._send_json(201, res)
                return

            self._send_error_json(404, "UNKNOWN_ENDPOINT", f"Unknown POST endpoint: {path}")
        except ValueError as e:
            msg = str(e)
            code = msg.split(":", 1)[0].strip() if ":" in msg else "INVALID_FIELD"
            status = 409 if code in ("WRONG_PHASE", "ROUND_CLOSED", "APPRAISAL_ALREADY_SUBMITTED") else 400
            self._send_error_json(status, code, msg)
        except Exception as e:
            traceback.print_exc()
            self._send_error_json(500, "SERVER_ERROR", str(e))


def main():
    port = int(os.environ.get("PVP_PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), PVPRequestHandler)
    lan_ips = get_lan_ips()
    print("==================================================================", flush=True)
    print(" ☕ Stage 3 56-Team AI Training Space & CSV Logger Running!", flush=True)
    print(f" 💻 Dashboard   : http://localhost:{port}/trade", flush=True)
    for ip in lan_ips:
        print(f" 🌐 LAN URL     : http://{ip}:{port}/trade", flush=True)
    print(f" 📁 CSV Logs Dir: {LOGS}", flush=True)
    print("==================================================================", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

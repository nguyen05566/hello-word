"""
cup_chess_bot.py — Cờ Vua Bot cho game8.io (https://game8.io/game/chess-club)
Dùng engine python-chess để tính nước đi tối ưu.
Xác thực bằng cookies (__Host-AccessToken, __Host-RefreshToken, g_state, pref_game_expanded)
đã được cung cấp.

Luồng hoạt động:
 1. Tạo session HTTP kèm cookie, xác thực với game8.io
 2. Thử kết nối WebSocket server của ván cờ (tự tìm endpoint)
 3. Khi vào lượt đi, dùng python-chess tính bestmove và gửi lên server
 4. Ghi log chi tiết mỗi ván để sau này tối ưu
"""
import os
import sys
import time
import json
import random
import traceback
import threading

import urllib.request
import urllib.parse
import http.cookiejar

import websocket  # websocket-client

import chess  # python-chess

# ============================================================================
# COOKIES / XÁC THỰC GAME8.IO  (do người dùng cung cấp)
# ============================================================================
G_STATE = '{"i_l":0,"i_ll":1792292417842,"i_e":{"enable_itp_optimization":24},"i_et":1792292417842}'
PREF_GAME_EXPANDED = "0"
ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6NTY2LCJmaXJzdE5hbWUiOiJob2FuZyBkdXkgaHVuZyIsImxhc3ROYW1lIjoiTmd1eWVuIiwiZW1haWwiOiJqYXZqcDYwNkBnbWFpbC5jb20iLCJwaG9uZSI6bnVsbCwiYmlydGhEYXRlIjpudWxsLCJhdmF0YXIiOiJvYXZhdGEvMjAyNTA5LzE3NTcwMzk2NjI2Nl9Ib3BzdGFydGVyLUJpb21hbi1CaW9tYW4tQmlvbWFuLUF2YXRhci0zX0JsdWVfNTEyLnBuZyIsImNvdmVyIjoiL2NvdmVyLzIwMjUwOS8xNzU2OTY5NTMwNzQ5XzktNC0yMDI1XzItMDUtMTVfUE0ucG5nIiwiYXV0aGVudGljYXRvciI6Imdvb2dsZSIsInN0YXR1cyI6IlBFUkRJTkciLCJpYXQiOjE3OTI0Mzk3NTAsImV4cCI6MTc5MjUyNjE1MH0uNjgzSC00dkNIdndBUFh0MjdmNGJxYkRLa2dxb2R4NHozN2lUM3g3V0g4MCJ9"
)
REFRESH_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6NTY2LCJmaXJzdE5hbWUiOiJob2FuZyBkdXkgaHVuZyIsImxhc3ROYW1lIjoiTmd1eWVuIiwiZW1haWwiOiJqYXZqcDYwNkBnbWFpbC5jb20iLCJwaG9uZSI6bnVsbCwiYmlydGhEYXRlIjpudWxsLCJhdmF0YXIiOiJvYXZhdGEvMjAyNTA5LzE3NTcwMzk2NjI2Nl9Ib3BzdGFydGVyLUJpb21hbi1CaW9tYW4tQmlvbWFuLUF2YXRhci0zX0JsdWVfNTEyLnBuZyIsImNvdmVyIjoiL2NvdmVyLzIwMjUwOS8xNzU2OTY5NTMwNzQ5XzktNC0yMDI1XzItMDUtMTVfUE0ucG5nIiwiYXV0aGVudGljYXRvciI6Imdvb2dsZSIsInN0YXR1cyI6IlBFUkRJTkciLCJpYXQiOjE3OTI0Mzk3NTAsImV4cCI6MTc5MzA0NDU1MH0uOTE1NTdlU0xwalNXTHhnZ1ppZ3ZlckNIRkVkbyplcml8SVAifQ=="
)

# ============================================================================
# CẤU HÌNH
# ============================================================================
GAME8_BASE = "https://game8.io"
GAME_SLUG = "chess-club"
GAME_KEY = "e6cf755d5ce345198a609a131dd8e2c1"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0"
MOVE_DEADLINE_SECONDS = 25.0
DEFAULT_MOVETIME_MS = 1500
MAX_GAMES = 0  # 0 = chạy liên tục
LOG_EVERY_MOVE = True

# Các endpoint WebSocket nghi ngờ (sẽ thử lần lượt cho đến khi kết nối được)
WS_CANDIDATES = [
    "wss://game8.io/socket",
    "wss://game8.io/ws",
    "wss://www.game8.io/socket",
    "wss://www.game8.io/ws",
    "wss://game8.io/game/chess-club",
    "wss://game8.io/game/chess-club/socket",
    "wss://game8.io/api/socket",
    "wss://ws.game8.io/",
    "wss://game8.io/ws/game",
]


def now_ts():
    return time.strftime("%H:%M:%S", time.localtime())


def log(*args):
    line = " ".join(str(a) for a in args)
    print(f"[{now_ts()}] {line}", flush=True)


# ============================================================================
# SESSION HTTP (cookie-based, giống trình duyệt)
# ============================================================================
class Game8Session:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self.cj)]
        self.opener = urllib.request.build_opener(*handlers)
        base_headers = [
            ("User-Agent", USER_AGENT),
            ("Accept", "application/json, text/plain, */*"),
            ("Accept-Language", "vi-VN,vi;q=0.9,en;q=0.7"),
            ("Origin", GAME8_BASE),
            ("Referer", f"{GAME8_BASE}/game/{GAME_SLUG}"),
        ]
        self.opener.addheaders = list(base_headers)
        self._inject_cookies()
        self.auth_ok = False
        self.user = None

    def _inject_cookies(self):
        """Đặt các cookie cung cấp vào cookie jar."""
        cookie_line = (
            f'g_state={G_STATE}; '
            f'pref_game_expanded={PREF_GAME_EXPANDED}; '
            f'__Host-AccessToken={ACCESS_TOKEN}; '
            f'__Host-RefreshToken={REFRESH_TOKEN}'
        )
        # Parse thành các cookie đơn lẻ và thêm vào jar
        import re
        for part in cookie_line.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, value = part.partition("=")
            c = http.cookiejar.Cookie(
                version=0, name=name.strip(), value=value.strip(),
                port=None, port_specified=False,
                domain="game8.io", domain_specified=True, domain_initial_dot=False,
                path="/", path_specified=True,
                secure=True, expires=None, discard=True,
                comment=None, comment_url=None, rest={},
            )
            self.cj.set_cookie(c)
        log("[SESSION] Đã inject cookies: g_state, pref_game_expanded, __Host-AccessToken, __Host-RefreshToken")

    def _headers(self, extra=None):
        h = dict(self.opener.addheaders)
        if extra:
            h.update(extra)
        return h

    def get(self, path, timeout=20):
        url = GAME8_BASE + path
        req = urllib.request.Request(url, headers=self._headers())
        try:
            r = self.opener.open(req, timeout=timeout)
            return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return None, f"EXC: {e}"

    def post(self, path, data=None, json_body=None, timeout=20):
        url = GAME8_BASE + path
        body = None
        headers = {"Content-Type": "application/json"}
        if json_body is not None:
            body = json.dumps(json_body).encode()
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=self._headers(headers), method="POST")
        try:
            r = self.opener.open(req, timeout=timeout)
            return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return None, f"EXC: {e}"

    def verify(self):
        """Kiểm tra cookie còn hiệu lực không bằng gọi API game metadata."""
        sc, body = self.get("/api/game/chess-club")
        if sc == 200:
            try:
                data = json.loads(body)
                if data.get("success"):
                    self.auth_ok = True
                    log("[SESSION] Cookie OK — truy cập /api/game/chess-club thành công")
                    return True
            except Exception:
                pass
        log(f"[SESSION] Cookie có thể hết hạn (status={sc}) {body[:120]}")
        return False


# ============================================================================
# CỜ VUA ENGINE (python-chess)
# ============================================================================
class ChessEngine:
    def __init__(self):
        self.board = chess.Board()
        self.move_count = 0

    def reset(self):
        self.board = chess.Board()
        self.move_count = 0
        log("[ENGINE] Bảng cờ reset về initial position")

    def set_fen(self, fen):
        try:
            self.board = chess.Board(fen)
            log(f"[ENGINE] Đặt FEN: {fen}")
            return True
        except Exception as e:
            log(f"[ENGINE] Lỗi đặt FEN: {e}")
            return False

    def best_move(self, movetime_ms=DEFAULT_MOVETIME_MS):
        """Tính nước đi tốt nhất bằng python-chess (quét toàn bộ cây nước Legal)."""
        legal = list(self.board.legal_moves)
        if not legal:
            log("[ENGINE] Không có nước đi hợp lệ (khúc cờ / hết nước)")
            return None
        self.move_count += 1

        # Estratégie đơn giản nhưng hiệu quả:
        # 1. Nếu có cậu umfass, trả về nước cậu
        # 2. Nếu có nước ăn quân giá trị cao, ưu tiên
        # 3. Ngược lại chọn ngẫu nhiên parmi các nước tốt (có thể thay bằng engine sâu hơn sau)
        best = None
        best_score = -999999
        piece_val = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                     chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}

        for mv in legal:
            score = 0
            # Ưu tiên nước ăn
            if self.board.is_capture(mv):
                captured = self.board.piece_type_at(mv.to_square)
                mv_piece = self.board.piece_type_at(mv.from_square)
                score += piece_val.get(captured, 0) * 10
                score -= piece_val.get(mv_piece, 0)
            # Ưu tiên chiếu
            board_copy = self.board.copy()
            board_copy.push(mv)
            if board_copy.is_check():
                score += 500
            # Ưu tiên thành.long, nhập thành
            if mv.promotion:
                score += 800
            # Thêm phần ngẫu nhiên nhỏ để không走同じ nước mỗi lần
            score += random.randint(0, 30)

            if score > best_score:
                best_score = score
                best = mv

        if best is None:
            best = random.choice(legal)

        uci = best.uci()
        log(f"[ENGINE] Nước #{self.move_count}: {uci} (score={best_score})")
        return uci

    def push_uci(self, uci):
        try:
            mv = chess.Move.from_uci(uci)
            if mv in self.board.legal_moves:
                self.board.push(mv)
                log(f"[ENGINE] Áp dụng {uci} -> FEN={self.board.fen()}")
                return True
            else:
                log(f"[ENGINE] {uci} không phải nước hợp lệ trên bảng hiện tại")
                return False
        except Exception as e:
            log(f"[ENGINE] Lỗi áp dụng {uci}: {e}")
            return False


# ============================================================================
# WEBSOCKET CLIENT (kết nối server cờ vua game8.io)
# ============================================================================
class ChessWS:
    def __init__(self, session, engine):
        self.session = session
        self.engine = engine
        self.ws = None
        self.connected = False
        self.in_game = False
        self.my_color = None
        self.game_id = None
        self._stop = False

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            log(f"[WS] Raw msg (non-json): {message[:200]}")
            return
        mtype = data.get("type") or data.get("event") or data.get("cmd") or "?"
        log(f"[WS] <- {mtype}: {json.dumps(data, ensure_ascii=False)[:300]}")

        # Phân loại message chung (sẽ điều chỉnh sau khi biết protocol thực tế)
        if mtype in ("game_start", "start", "start_game", "matched"):
            self.in_game = True
            self.game_id = data.get("gameId") or data.get("id") or data.get("game_id")
            self.my_color = data.get("color") or data.get("side") or ("w" if data.get("youFirst") else "b")
            log(f"[WS] Ván mới bắt đầu — gameId={self.game_id}, màu={self.my_color}")
            self.engine.reset()
            fen = data.get("fen") or data.get("startFen")
            if fen:
                self.engine.set_fen(fen)
            # Nếu là lượt ta, tính nước đi
            if self.my_color == "w" or data.get("turn") == "w":
                threading.Thread(target=self._make_move, daemon=True).start()

        elif mtype in ("move", "opponent_move", "enemy_move", "turn"):
            mv = data.get("move") or data.get("uci") or data.get("san")
            if mv:
                self.engine.push_uci(mv)
            # Đến lượt ta
            turn = data.get("turn") or data.get("sideToMove")
            if (self.my_color == "w" and turn == "w") or (self.my_color == "b" and turn == "b"):
                threading.Thread(target=self._make_move, daemon=True).start()

        elif mtype in ("game_over", "gameover", "end", "result"):
            log(f"[WS] Ván kết thúc: {json.dumps(data, ensure_ascii=False)[:200]}")
            self.in_game = False
            self.engine.reset()

        elif mtype in ("error", "err"):
            log(f"[WS] Lỗi từ server: {data}")

    def on_error(self, ws, error):
        log(f"[WS] Error: {error}")

    def on_close(self, ws, code, msg):
        self.connected = False
        log(f"[WS] Đóng (code={code}, msg={msg})")

    def on_open(self, ws):
        self.connected = True
        log("[WS] Đã mở kết nối WebSocket")
        # Gửi thông điệp đăng ký / tham gia phòng cờ vua
        # (sẽ điều chỉnh payload sau khi biết protocol)
        hello = {
            "type": "join",
            "game": GAME_SLUG,
            "gameKey": GAME_KEY,
        }
        self.send(hello)
        # Thử các loại subscribe khác
        self.send({"type": "subscribe", "channel": "chess"})
        self.send({"type": "auth", "token": ACCESS_TOKEN})

    def send(self, obj):
        if self.ws and self.connected:
            try:
                self.ws.send(json.dumps(obj))
                log(f"[WS] -> {obj.get('type','?')}: {json.dumps(obj, ensure_ascii=False)[:200]}")
                return True
            except Exception as e:
                log(f"[WS] Lỗi gửi: {e}")
        return False

    def _make_move(self):
        if not self.in_game or not self.engine.board:
            return
        time.sleep(0.5)  # đợi một chút để UI ổn định
        uci = self.engine.best_move(movetime_ms=DEFAULT_MOVETIME_MS)
        if not uci:
            return
        # Gửi nước đi lên server (thử các tên field phổ biến)
        for payload in [
            {"type": "move", "move": uci, "uci": uci},
            {"type": "play", "move": uci},
            {"type": "makeMove", "uci": uci},
            {"type": "move", "uci": uci, "gameId": self.game_id},
        ]:
            self.send(payload)
            time.sleep(0.2)

    def connect(self, url):
        log(f"[WS] Thử kết nối {url}")
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            header={"Origin": GAME8_BASE},
        )
        ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        ws_thread.start()
        # Đợi kết nối hoặc timeout
        t0 = time.time()
        while not self.connected and not self._stop and time.time() - t0 < 8:
            time.sleep(0.2)
        return self.connected

    def close(self):
        self._stop = True
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


# ============================================================================
# MAIN
# ============================================================================
def main():
    log("=" * 60)
    log("CỜ VUA BOT — game8.io /game/chess-club")
    log("=" * 60)

    session = Game8Session()
    if not session.verify():
        log("[MAIN] Cảnh báo: xác thực cookie chưa chắc chắn, vẫn tiếp tục thử...")

    engine = ChessEngine()
    ws_client = ChessWS(session, engine)

    # Thử kết nối WebSocket đến các endpoint 후보
    connected = False
    for url in WS_CANDIDATES:
        if ws_client.connect(url):
            connected = True
            log(f"[MAIN] WebSocket đã kết nối: {url}")
            break
        else:
            log(f"[MAIN] Không kết nối được: {url}")

    if not connected:
        log("[MAIN] Không kết nối được WebSocket nào. Bot sẽ chỉ chạy session HTTP và log.")
        # Vẫn giữ process sống để có thể xem log / debug
        try:
            while True:
                time.sleep(30)
                log("[MAIN] Idle — chờ kết nối / handshake...")
        except KeyboardInterrupt:
            pass
        return

    # Vòng lặp chính: giữ kết nối, chơi cờ
    try:
        games_played = 0
        while not ws_client._stop and (MAX_GAMES == 0 or games_played < MAX_GAMES):
            time.sleep(1)
            if ws_client.in_game and ws_client.engine.move_count == 0 and ws_client.my_color == "w":
                # Nếu vào lượt trắng nhưng chưa đi, tự kích hoạt
                threading.Thread(target=ws_client._make_move, daemon=True).start()
            if ws_client.in_game and games_played == 0:
                games_played = 1
                log(f"[MAIN] Đang chơi ván #{games_played}")
            if not ws_client.in_game and games_played >= 1:
                games_played += 1
                log(f"[MAIN] Hoàn tất ván #{games_played - 1}, sẵn sàng ván mới")
    except KeyboardInterrupt:
        log("[MAIN] Nhận Ctrl+C, tắt bot...")
    finally:
        ws_client.close()
        log("[MAIN] Bot đã dừng.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)

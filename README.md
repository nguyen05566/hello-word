# hello-word

## cup_bot_hoanchinh.py — Bot Cờ Úp (engine AB-JChess)

### Chạy local
```bash
pip install -r requirements.txt
# can binary AB-JChess + file abjchess-*.nnue cung thu muc voi file .py
python cup_bot_hoanchinh.py
```

### FIX WebSocket (mất kết nối)
Các thay đổi trong `JieqiCupBot` để bot không còn rớt WS:
1. **Chống callback/thread của ws cũ phá ws mới**: thêm `_is_stale(ws)` — mọi
   `on_open/on_message/on_error/on_close/on_ping/on_pong` bỏ qua nếu ws đã bị
   thay thế; `connect()` đóng và `join()` thread ws cũ trước khi mở ws mới.
2. **Reconnect khi socket chết lặng**: `_disconnect(reason)` đóng ws và ép
   `connected=False`, không phụ thuộc `on_close` (on_close không gọi tới khi
   socket chết không FIN). `send_message` lỗi cũng gọi `_disconnect`.
3. **Ping/Pong thật**: `run_forever(ping_interval=20)` + callback `on_ping`/
   `on_pong` cập nhật `last_recv_timestamp` (bản cũ im lặng 120s mới phát hiện).
4. **Watchdog trong run()**: 60s không frame khi đang đánh, 120s khi chờ bàn,
   45s chưa login xong, 300s ván treo -> reconnect ngay.
5. **Login fail không treo**: server từ chối LOGIN -> refresh session tối đa 3
   lần rồi reconnect (bản cũ treo vĩnh viễn ở trạng thái connected-chưa-login).
6. **1 thread keep-alive duy nhất** (`_keep_alive_started`) thay vì mỗi lần
   reconnect lại sinh thêm 1 thread PING.

### CI — `.github/workflows/run-bot.yml`
Tự chạy khi `push` vào `main`, hoặc trigger tay qua `workflow_dispatch`:

1. **Cai dat thu vien he thong**: build-essential, clang, git, curl, unzip
2. **Cai dat thu vien Python**: `pip install -r requirements.txt`
3. **Lay engine AB-JChess + NNUE** (cache `abjchess-v0.2b-avx2`):
   - Ưu tiên binary build sẵn trong release `engines-*` của `brianhliou/mistboard`
   - Fallback: build từ source `lxsgx23/AB-JChess` @ `1ae95ca` (v0.2b, clang avx2 static)
   - NNUE `abjchess-*.nnue` tải từ release v0.2b của tác giả (bắt buộc)
4. **Smoke test engine**: chạy `uci` thật — thiếu binary/NNUE là CI đỏ ngay
5. **Kiem tra cu phap** (`py_compile`) + **Chay bot** 10 phút (`timeout 600`)

Secrets nên set: `CARO_USER19`, `CARO_PASSWD19`

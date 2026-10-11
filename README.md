# hello-word

## cup_bot_hoanchinh.py — Bot Cờ Úp (engine AB-JChess)

### Chạy local
```bash
pip install -r requirements.txt
# can binary AB-JChess + file abjchess-*.nnue cung thu muc voi file .py
python cup_bot_hoanchinh.py
```

### CI — `.github/workflows/run-bot.yml`
Tự chạy khi `push` vào `main`, hoặc trigger tay qua `workflow_dispatch`:

1. **Cai dat thu vien he thong**: build-essential, clang, git, curl, unzip
2. **Cai dat thu vien Python**: `pip install -r requirements.txt`
3. **Lay engine AB-JChess + NNUE** (cache `abjchess-v0.2b-avx2`):
   - Ưu tiên binary build sẵn trong release `engines-*` của `brianhliou/mistboard`
   - Fallback: build từ source `lxsgx23/AB-JChess` @ `1ae95ca` (v0.2b, clang avx2 static)
   - NNUE `abjchess-*.nnue` tải từ release v0.2b của tác giả (bắt buộc)
4. **Smoke test engine**: chạy `uci` thật — thiếu binary/NNUE là CI đỏ ngay
5. **Kiem tra cu phap** + **Chay bot** 10 phút (`timeout 600`)

Secrets nên set: `CARO_USER19`, `CARO_PASSWD19`

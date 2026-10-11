# hello-word

## cup_bot_hoanchinh.py (Cờ Úp Bot — AB-JChess)

File bot được chia thành `parts/part_01.py … part_04.py` (vượt giới hạn 20KB/tool).

### Ghép file
```bash
bash assemble.sh     # -> cup_bot_hoanchinh.py
```

### Chạy local
```bash
pip install -r requirements.txt
python cup_bot_hoanchinh.py
```

### CI (GitHub Actions)
Workflow: `.github/workflows/run-bot.yml`
- Tự chạy khi `push` vào `main`, hoặc trigger tay qua `workflow_dispatch`
- Cài thư viện (`requirements.txt`) → ghép parts → kiểm tra cú pháp → chạy bot 10 phút

Secrets nên set trong repo: `CARO_USER19`, `CARO_PASSWD19`,
`MISTBOARD_ABJCHESS_ENGINE`, `MISTBOARD_ABJCHESS_NNUE`


class VisibleBoard:
    """Tracks actual piece positions for 'see-all' mode.

    Maintains a 90-cell board with actual piece types (from raw_face).
    Used to generate standard xiangqi FEN for the engine.
    """
    TYPE_TO_FEN = {1: 'k', 2: 'a', 3: 'b', 4: 'r', 5: 'c', 6: 'n', 7: 'p'}

    def __init__(self):
        self.cells = ['.'] * 90  # 90 positions, '.' = empty
        self.side_to_move = 'w'

    def reset(self):
        self.cells = ['.'] * 90
        self.side_to_move = 'w'

    def set_from_pieces(self, pieces, flip):
        """Set board from START_MATCH pieces list.
        pieces: [(sid, face, position, is_open), ...]
        flip: board flip state (from XiangqiBoardTracker)
        """
        self.cells = ['.'] * 90
        for sid, face, position, is_open in pieces:
            if position < 0 or position >= 90:
                continue
            if len(face) >= 2:
                color = face[0]  # 'r' or 'b'
                piece_type = int(face[1])
                fen_char = self.TYPE_TO_FEN.get(piece_type, '?')
                if color == 'r':
                    fen_char = fen_char.upper()
                self.cells[position] = fen_char

    def apply_move(self, source_pos, target_pos):
        """Move a piece from source to target."""
        if 0 <= source_pos < 90 and 0 <= target_pos < 90:
            self.cells[target_pos] = self.cells[source_pos]
            self.cells[source_pos] = '.'

    def flip_side(self):
        self.side_to_move = 'b' if self.side_to_move == 'w' else 'w'

    def to_fen(self, flip=False):
        """Convert to FEN string.
        Server positions: pos = row * 9 + col (row 0 = RED bottom, row 9 = BLACK top)
        FEN format: row 9 first (top), row 0 last (bottom)
        """
        rows = []
        for board_row in range(9, -1, -1):  # row 9 → row 0
            fen_str = ""
            empty = 0
            for col in range(9):
                pos = board_row * 9 + col
                piece = self.cells[pos] if 0 <= pos < 90 else '.'
                if piece == '.':
                    empty += 1
                else:
                    if empty > 0:
                        fen_str += str(empty)
                        empty = 0
                    fen_str += piece
            if empty > 0:
                fen_str += str(empty)
            rows.append(fen_str)
        return '/'.join(rows) + ' ' + self.side_to_move + ' - - 0 1'


class FlipchessAbjchessEngine:
    """AB-JChess 0.2d engine wrapper, UCI protocol, Pikafish-derived Jieqi NNUE.

    Khác biệt so với FlipchessJieqiEngine (PikaJieQi):
    - NNUE là BẮT BUỘC (phải load được EvalFile trước khi isready)
    - Binary tên 'AB-JChess' (Linux build) hoặc 'AB-JChess.exe' (Windows)
    - setoption thêm: AggressiveLevel, ChasingRule (riêng của AB-JChess)
    - Còn lại UCI tương thích ngược với PikaJieQi (FEN BAG, reveal suffix)
    """

    def __init__(self):
        self.proc = None
        self.binary_path = None
        self.nnue_path = None
        self.engine_lock = threading.Lock()
        self._readyok = False
        self._latest_bestmove = None
        self._engine_searching = False
        self._last_depth = "?"
        self._last_score = "?"
        self._restart_count = 0
        self._stdout_lines = []
        self._lines_lock = threading.Lock()
        for path in ABJCHESS_BINARY_CANDIDATES:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                self.binary_path = path
                break
        if not self.binary_path:
            print(f"[ENGINE] ❌ Không tìm thấy AB-JChess binary. Đã thử: {ABJCHESS_BINARY_CANDIDATES}")
            print(f"[ENGINE] ❌ Build từ source: cd AB-JChess/abjchess && make -j2 build ARCH=x86-64-bmi2 COMP=gcc")
            print(f"[ENGINE] ❌ Hoặc download Windows build: https://github.com/lxsgx23/AB-JChess/releases/tag/v0.2d")
            self.engine = False
            return
        # ★ Tìm file NNUE — BẮT BUỘC cho AB-JChess (khác PikaJieQi)
        for path in ABJCHESS_NNUE_CANDIDATES:
            if path and os.path.isfile(path):
                self.nnue_path = path
                break
        if not self.nnue_path:
            print(f"[ENGINE] ❌ Không tìm thấy file NNUE abjchess-*.nnue.")
            print(f"[ENGINE] ❌ Download từ https://github.com/lxsgx23/AB-JChess/releases/tag/v0.2d (0.2d_bmi2.zip)")
            print(f"[ENGINE] ❌ Hoặc override qua env MISTBOARD_ABJCHESS_NNUE=/path/to/abjchess-*.nnue")
            self.engine = False
            return
        print(f"[ENGINE] 🎯 AB-JChess 0.2d (flipchess) = {self.binary_path}")
        print(f"[ENGINE] 🎯 NNUE = {self.nnue_path}")

        self._init_engine()
        self.engine = self.proc is not None

    def _init_engine(self):
        if self.proc:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=2)
            except Exception:
                pass
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None
        try:
            self.proc = subprocess.Popen(
                [self.binary_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                cwd=os.path.dirname(self.binary_path),
            )
        except Exception as e:
            print(f"[ENGINE] ❌ Popen error: {e}")
            return

        def consume_stderr(proc):
            try:
                while proc.poll() is None:
                    line = proc.stderr.readline()
                    if not line: break
                    line = line.strip()
                    if line:
                        print(f"[PKJQ-DBG] {line}")
            except Exception:
                pass
        threading.Thread(target=consume_stderr, args=(self.proc,), daemon=True).start()

        def consume_stdout(proc):
            while True:
                try:
                    line = proc.stdout.readline()
                except Exception:
                    break
                if not line: break
                line = line.strip()
                if not line: continue
                with self._lines_lock:
                    self._stdout_lines.append(line)
                    if len(self._stdout_lines) > 200:
                        self._stdout_lines = self._stdout_lines[-100:]
                if line == "readyok":
                    self._readyok = True
                if line.startswith("info string"):
                    print(f"[ENGINE-MSG] {line}", flush=True)
                if line.startswith("bestmove"):
                    self._latest_bestmove = line
                    self._engine_searching = False
        threading.Thread(target=consume_stdout, args=(self.proc,), daemon=True).start()

        with self.engine_lock:
            try:
                self.proc.stdin.write("uci\n")
                self.proc.stdin.flush()
            except Exception as e:
                print(f"[ENGINE] ❌ Send uci error: {e}")
                return
        if not self._wait_for_line("uciok", timeout=30):
            print("[ENGINE] ❌ uciok timeout")
            self._kill()
            return
        # ★ AB-JChess: NNUE BẮT BUỘC — phải gửi EvalFile trước isready.
        # (PikaJieQi cũ chỉ gửi pikafish.nnue nếu file tồn tại; AB-JChess thì bắt buộc.)
        with self.engine_lock:
            try:
                _threads = max(1, min(2, (os.cpu_count() or 2) - 1))
                self.proc.stdin.write(f"setoption name Threads value {_threads}\n")
                self.proc.stdin.write("setoption name Hash value 128\n")
                # ★ AB-JChess yêu cầu EvalFile — không có thì engine crash khi go.
                self.proc.stdin.write(f"setoption name EvalFile value {self.nnue_path}\n")
                self.proc.stdin.write("setoption name MultiPV value 1\n")
                # ★ 2 option riêng của AB-JChess (giá trị mặc định theo README)
                self.proc.stdin.write(f"setoption name AggressiveLevel value {ENGINE_USE_AGGRESSIVE_LEVEL}\n")
                self.proc.stdin.write(f"setoption name ChasingRule value {ENGINE_CHASING_RULE}\n")
                self.proc.stdin.write("isready\n")
                self.proc.stdin.flush()
            except Exception as e:
                print(f"[ENGINE] ❌ Config error: {e}")
                return
        if not self._wait_for_readyok(timeout=3


CMD_NAMES = {
    300: "PONG", 301: "PING", 302: "LOGIN", 303: "ALERT",
    311: "BROADCAST", 314: "SET_CLIENT_MODE", 315: "CONFIG",
    331: "CHAT.SEND", 335: "CHAT.MSG",
    401: "ENTER_PLACE", 405: "CREATE_RULE", 406: "PLAYER_ENTERED", 407: "PLAYER_EXITED",
    408: "QUICK_PLAY", 410: "KICK_PLAYER", 412: "LIST_ZONE_ROOM", 413: "LIST_BET_AMT",
    414: "GET_TABLE_DATA", 416: "SLOT_IN_TABLE_CHANGED",
    417: "START_MATCH", 418: "GAMEOVER", 419: "ENTER_STATE",
    420: "SET_TURN", 434: "SET_READY",
    502: "PLAY", 529: "MOVE", 533: "ASK_DRAW", 534: "SURRENDER", 601: "LOGIN_EX",
}

class Conn:
    def pack(self, cmd, data=b''):
        result = bytearray()
        if isinstance(cmd, str):
            cmd_bytes = cmd.encode('ascii')
            result.append((-len(cmd_bytes)) & 0xFF)
            result.extend(cmd_bytes)
        elif isinstance(cmd, int):
            result.extend(struct.pack('>H', cmd))
        result.extend(data)
        return bytes(result)

    def pack_byte(self, value): return struct.pack('>b', value)
    def pack_int(self, value): return struct.pack('>i', value)
    def pack_ascii(self, value):
        encoded = value.encode('ascii')[:255]
        return struct.pack('>b', len(encoded)) + encoded
    def pack_string(self, value):
        encoded = value.encode('utf-16-be')
        return struct.pack('>h', len(encoded) // 2) + encoded

class InboundMessage:
    def __init__(self, data):
        self.data = bytes(data)
        self.offset = 0
        self.command = self._parse_command()

    def _parse_command(self):
        length = self.read_byte()
        if length < 0:
            cmd = self.data[self.offset:self.offset + (-length)].decode('ascii', errors='replace')
            self.offset += (-length)
            return cmd
        else:
            next_byte = self.data[self.offset] & 0xFF
            self.offset += 1
            return CMD_NAMES.get((length << 8) | next_byte, str((length << 8) | next_byte))

    def read_byte(self):
        val = struct.unpack_from('>b', self.data, self.offset)[0]
        self.offset += 1
        return val
    def read_short(self):
        val = struct.unpack_from('>h', self.data, self.offset)[0]
        self.offset += 2
        return val
    def read_int(self):
        val = struct.unpack_from('>i', self.data, self.offset)[0]
        self.offset += 4
        return val
    def read_long(self):
        val = struct.unpack_from('>q', self.data, self.offset)[0]
        self.offset += 8
        return val
    def read_ascii(self):
        length = self.read_byte()
        if length < 0: length += 256
        s = self.data[self.offset:self.offset + length].decode('ascii', errors='replace')
        self.offset += length
        return s
    def read_string(self):
        char_count = self.read_short()
        s = self.data[self.offset:self.offset + char_count * 2].decode('utf-16-be', errors='replace')
        self.offset += char_count * 2
        return s
    def rem(self):
        return len(self.data) - self.offset

STANDARD_PAWN_POSITIONS = set()
for _c in [0, 2, 4, 6, 8]:
    STANDARD_PAWN_POSITIONS.add(6 * 9 + _c)
    STANDARD_PAWN_POSITIONS.add(3 * 9 + _c)

INITIAL_BAG = {'A': 2, 'B': 2, 'N': 2, 'R': 2, 'C': 2, 'P': 5,
               'a': 2, 'b': 2, 'n': 2, 'r': 2, 'c': 2, 'p': 5}
BAG_ORDER = ['A', 'B', 'N', 'R', 'C', 'P', 'a', 'b', 'n', 'r', 'c', 'p']

UCI_MOVE_RE = re.compile(r'^[a-i]\d[a-i]\d$')
UCI_MOVE_WITH_SUFFIX_RE = re.compile(r'^[a-i]\d[a-i]\d[a-zA-Z]{0,2}$')

class XiangqiBoardTracker:
    INITIAL_FEN = "xxxxkxxxx/9/1x5x1/x1x1x1x1x/9/9/X1X1X1X1X/1X5X1/9/XXXXKXXXX w"

    def __init__(self):
        self.reset()

    def reset(self):
        self.start_fen = self.INITIAL_FEN.split(' ')[0]
        self.start_side = 'w'
        self.uci_moves = []
        self.revealed_chars = []
        self.my_slot_id = -1
        self.first_turn_slot_id = 0
        self.is_my_turn = False
        self.is_playing = False
        self.is_red = None
        self.dark_positions = set()
        self.flip = False
        self.flip_known = False
        self.side_to_move = 'w'

    def pos_to_rc(self, pos):
        s_row, col = pos // 9, pos % 9
        return ((9 - s_row) if self.flip else s_row), col

    def rc_to_pos(self, fen_row, col):
        s_row = (9 - fen_row) if self.flip else fen_row
        return s_row * 9 + col

    def pos_to_engine_move(self, source_pos, target_pos):
        s_row, s_col = self.pos_to_rc(source_pos)
        t_row, t_col = self.pos_to_rc(target_pos)
        return (f"{chr(ord('a') + s_col)}{9 - s_row}"
                f"{chr(ord('a') + t_col)}{9 - t_row}")

    def engine_move_to_pos(self, engine_move):
        move = engine_move[:4]
        s_col, s_rank = ord(move[0]) - ord('a'), int(move[1])
        t_col, t_rank = ord(move[2]) - ord('a'), int(move[3])
        return (self.rc_to_pos(9 - s_rank, s_col),
                self.rc_to_pos(9 - t_rank, t_col))

    def bag_string(self):
        bag = dict(INITIAL_BAG)
        for ch in self.revealed_chars:
            if ch in bag:
                bag[ch] = max(0, bag[ch] - 1)
        return "".join(f"{k}{bag[k]}" for k in BAG_ORDER)

    def get_current_fen(self):
        fen = f"{self.start_fen} {self.bag_string()} {self.start_side} - - 0 1"
        moves = [m for m in self.uci_moves if UCI_MOVE_WITH_SUFFIX_RE.match(m)]
        return fen, moves

    def set_base(self, board_fen, side='w'):
        board_fen = board_fen.split(' ')[0] if ' ' in board_fen else board_fen
        self.start_fen = board_fen
        self.start_side = side
        self.side_to_move = side
        self.uci_moves = []
        self.revealed_chars = []
        self.dark_positions.clear()

    def record_move(self, mv, revealed_char=None):
        uci = mv + (revealed_char or "")
        self.uci_moves.append(uci)
        if revealed_char:
            for _ch in revealed_char:
                self.revealed_chars.append(_ch)
        self.side_to_move = 'b' if self.side_to_move == 'w' else 'w'
        return uci

    def set_my_slot(self, slot_id, first_turn_slot_id):
        self.my_slot_id = slot_id
        self.first_turn_slot_id = first_turn_slot_id
        self.is_red = (self.my_slot_id == self.first_turn_slot_id)

    def detect_flip(self, pieces):
        red_rows, black_rows = [], []
        red_king_row = black_king_row = None
        for sid, face, position, is_open in pieces:
            if position is None or position < 0 or position >= 90:
                continue
            row = position // 9
            color = face[0] if face else (sid[0] if sid else 'r')
            ptype = int(face[1]) if len(face) > 1 and str(face[1]).isdigit() else 0
            if ptype == 0 and len(sid) > 1 and str(sid[1]).isdigit():
                ptype = int(sid[1])
            if color == 'r':
                red_rows.append(row)
                if ptype == 1: red_king_row = row
            else:
                black_rows.append(row)
                if ptype == 1: black_king_row = row
        if red_king_row is not None and black_king_row is not None:
            self.flip = red_king_row < black_king_row
            self.flip_known = True
        elif red_king_row is not None:
            self.flip = red_king_row <= 4
            self.flip_known = True
        elif black_king_row is not None:
            self.flip = black_king_row >= 5
            self.flip_known = True
        elif red_rows and black_rows:
            self.flip = (sum(red_rows) / len(red_rows)) < (sum(black_rows) / len(black_rows))
            self.flip_known = True
        else:
            self.flip = bool(self.is_red)
            self.flip_known = False
        return self.flip

    def sanity_check_fen(self, board_fen):
        rows = board_fen.split(' ')[0].split('/')
        if len(rows) != 10:
            return False, f"FEN có {len(rows)} hàng"
        K_row = k_row = None
        for i, r in enumerate(rows):
            if 'K' in r: K_row = i
            if 'k' in r: k_row = i
        if K_row is None or k_row is None:
            return False, "thiếu tướng"
        if K_row < 7 or k_row > 2:
            return False, f"tướng sai chiều (K hàng {K_row}, k hàng {k_row})"
        return True, "ok"

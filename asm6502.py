# Auto-generated from coolgirl_builder.py
# Do not edit by hand unless you know what you are doing.

from config import *

OPC = {
    ('lda','imm'):0xA9, ('lda','zp'):0xA5, ('lda','abs'):0xAD,
    ('lda','absx'):0xBD, ('lda','absy'):0xB9, ('lda','indy'):0xB1,
    ('lda','zpx'):0xB5,
    ('sta','zp'):0x85,  ('sta','abs'):0x8D, ('sta','absx'):0x9D,
    ('sta','absy'):0x99,('sta','indy'):0x91,
    ('sta','zpx'):0x95,
    ('stx','zp'):0x86,  ('stx','abs'):0x8E,
    ('sty','zp'):0x84,  ('sty','abs'):0x8C,
    ('ldx','imm'):0xA2, ('ldx','zp'):0xA6, ('ldx','abs'):0xAE,
    ('ldy','imm'):0xA0, ('ldy','zp'):0xA4, ('ldy','abs'):0xAC,
    ('adc','imm'):0x69, ('adc','zp'):0x65, ('adc','abs'):0x6D,
    ('sbc','imm'):0xE9, ('sbc','zp'):0xE5, ('sbc','abs'):0xED,
    ('cmp','imm'):0xC9, ('cmp','zp'):0xC5, ('cmp','abs'):0xCD, ('cmp','indy'):0xD1,
    ('cpy','imm'):0xC0, ('cpx','imm'):0xE0,
    ('and','imm'):0x29, ('and','zp'):0x25, ('and','abs'):0x2D,
    ('ora','imm'):0x09, ('ora','zp'):0x05, ('ora','abs'):0x0D,
    ('eor','imm'):0x49, ('eor','zp'):0x45, ('eor','abs'):0x4D,
    ('bit','abs'):0x2C,
    ('jsr','abs'):0x20, ('jmp','abs'):0x4C,
    ('beq','rel'):0xF0, ('bne','rel'):0xD0, ('bcc','rel'):0x90, ('bcs','rel'):0xB0,
    ('bmi','rel'):0x30, ('bpl','rel'):0x10,
    ('pha','impl'):0x48, ('pla','impl'):0x68,
    ('php','impl'):0x08, ('plp','impl'):0x28,
    ('txa','impl'):0x8A, ('tax','impl'):0xAA,
    ('tya','impl'):0x98, ('tay','impl'):0xA8,
    ('txs','impl'):0x9A, ('tsx','impl'):0xBA,
    ('rts','impl'):0x60, ('rti','impl'):0x40,
    ('sei','impl'):0x78, ('cli','impl'):0x58,
    ('cld','impl'):0xD8, ('sed','impl'):0xF8,
    ('clc','impl'):0x18, ('sec','impl'):0x38,
    ('clv','impl'):0xB8,
    ('iny','impl'):0xC8, ('dey','impl'):0x88,
    ('dex','impl'):0xCA, ('inx','impl'):0xE8,
    ('nop','impl'):0xEA,
    ('inc','zp'):0xE6, ('inc','abs'):0xEE,
    ('inc','zpx'):0xF6, ('inc','absx'):0xFE,
    ('dec','zp'):0xC6, ('dec','abs'):0xCE,
    ('dec','zpx'):0xD6,
    ('asl','acc'):0x0A, ('asl','zp'):0x06, ('asl','abs'):0x0E,
    ('lsr','acc'):0x4A, ('lsr','zp'):0x46, ('lsr','abs'):0x4E,
    ('rol','acc'):0x2A, ('rol','zp'):0x26, ('rol','abs'):0x2E,
    ('ror','acc'):0x6A, ('ror','zp'):0x66, ('ror','abs'):0x6E,
}

BRANCHES = {'beq','bne','bcc','bcs','bmi','bpl'}

def _split_comment(line: str) -> str:
    if ';' in line:
        line = line.split(';', 1)[0]
    return line.rstrip()

def _parse_num(tok, labels=None):
    tok = tok.strip()
    if not tok:
        raise ValueError("empty token")
    if labels is not None and tok in labels:
        return labels[tok]
    if tok.startswith('$'):
        return int(tok[1:], 16)
    if tok.startswith('%'):
        return int(tok[1:], 2)
    if tok.lstrip('-').isdigit():
        return int(tok, 10)
    if labels is not None:
        raise KeyError(tok)
    raise ValueError(f"bad numeric token: {tok}")

def _detect_mode(mn, op, labels=None):
    if op is None or op == "":
        return 'impl'
    op = op.strip()
    if mn in BRANCHES:
        return 'rel'
    if op.lower() == 'a':
        return 'acc'
    if op.startswith('#'):
        return 'imm'
    if op.startswith('(') and op.endswith('),y'):
        return 'indy'
    if op.endswith(',x'):
        # Решаем: zpx или absx? Если значение помещается в zp (0..$FF)
        # и опкод поддерживает zpx — выбираем zpx (компактнее на 1 байт).
        try:
            base = op[:-2].strip()
            val = _parse_num(base, labels)
            if 0 <= val <= 0xFF and (mn, 'zpx') in OPC:
                return 'zpx'
        except Exception:
            pass
        return 'absx'
    if op.endswith(',y'):
        return 'absy'
    if mn in ('jsr','jmp','bit'):
        return 'abs'
    try:
        val = _parse_num(op, labels)
        return 'zp' if (0 <= val <= 0xFF and (mn,'zp') in OPC) else 'abs'
    except Exception:
        return 'abs'

def assemble_6502(src: str, origin: int) -> bytes:
    lines = []
    for raw in src.splitlines():
        line = _split_comment(raw).strip()
        if not line:
            continue
        lines.append(line)

    labels = {}
    pc = origin
    for line in lines:
        if line.endswith(':'):
            labels[line[:-1]] = pc
            continue
        if line.startswith('.byte'):
            args = [a.strip() for a in line[5:].split(',') if a.strip()]
            pc += len(args)
            continue
        parts = line.split(None, 1)
        mn   = parts[0].lower()
        op   = parts[1].strip() if len(parts) > 1 else None
        mode = _detect_mode(mn, op)
        if mode in ('impl','acc'):
            pc += 1
        elif mode in ('imm','zp','rel','indy','zpx'):
            pc += 2
        elif mode in ('abs','absx','absy'):
            pc += 3
        else:
            raise ValueError(f"unsupported mode pass1: {line}")

    out = bytearray()
    pc  = origin
    for line in lines:
        if line.endswith(':'):
            continue
        if line.startswith('.byte'):
            args = [a.strip() for a in line[5:].split(',') if a.strip()]
            for a in args:
                out.append(_parse_num(a, labels) & 0xFF)
                pc += 1
            continue
        parts = line.split(None, 1)
        mn   = parts[0].lower()
        op   = parts[1].strip() if len(parts) > 1 else None
        mode = _detect_mode(mn, op, labels)
        key  = (mn, mode)
        if key not in OPC:
            raise ValueError(f"unsupported instruction: {line} -> {key}")
        out.append(OPC[key])
        pc += 1
        if mode in ('impl','acc'):
            continue
        if mode == 'imm':
            out.append(_parse_num(op[1:], labels) & 0xFF); pc += 1; continue
        if mode == 'zp':
            out.append(_parse_num(op, labels) & 0xFF);     pc += 1; continue
        if mode == 'zpx':
            base = op[:-2].strip()
            out.append(_parse_num(base, labels) & 0xFF);   pc += 1; continue
        if mode == 'indy':
            out.append(_parse_num(op[1:-3].strip(), labels) & 0xFF); pc += 1; continue
        if mode == 'rel':
            target = _parse_num(op, labels)
            rel    = target - (pc + 1)
            if rel < -128 or rel > 127:
                raise ValueError(f"branch out of range: {line}")
            out.append(rel & 0xFF); pc += 1; continue
        if mode in ('abs','absx','absy'):
            base = op[:-2].strip() if mode != 'abs' else op
            v    = _parse_num(base, labels) & 0xFFFF
            out.append(v & 0xFF); out.append((v >> 8) & 0xFF); pc += 2; continue
        raise ValueError(f"unexpected mode pass2: {line}")

    return bytes(out)

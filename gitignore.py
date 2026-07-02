"""gitignore 格式規則比對（純 Python 標準庫實作，不需安裝任何額外套件）。

實作 https://git-scm.com/docs/gitignore 定義的比對規則：
- ``#`` 開頭為註解、空白行跳過、結尾未跳脫的空白忽略
- ``!`` 開頭為反向規則（原本被忽略的檔案改為不忽略）
- 結尾 ``/`` 代表只比對資料夾
- 開頭或中間含 ``/`` 的規則定錨於根目錄，否則任何層級都比對
- ``*``、``?``、``[...]`` 萬用字元（``*`` 與 ``?`` 不跨越 ``/``）
- ``**`` 獨立成段時代表任意層級（``**/foo``、``a/**/b``、``foo/**``）
- 反斜線 ``\\`` 跳脫下一個字元（如 ``\\#``、``\\!``、``\\ ``）

比對慣例：路徑一律用 ``/`` 分隔、相對於根目錄；資料夾請在結尾加上 ``/``。
"""

import re


class GitIgnoreSpec:
    """一組 gitignore 規則；與 git 相同，以「最後一條符合的規則」決定是否忽略。"""

    def __init__(self, rules):
        self._rules = rules  # [(compiled_regex, include)]

    @classmethod
    def from_lines(cls, lines):
        """逐行編譯規則。任一行格式錯誤會拋出 ValueError（訊息含該行原始內容）。"""
        rules = []
        for line in lines:
            compiled = _compile_line(line)
            if compiled is not None:
                rules.append(compiled)
        return cls(rules)

    def match_file(self, path):
        matched = False
        for regex, include in self._rules:
            if regex.match(path):
                matched = include
        return matched


def _compile_line(line):
    """把一行規則編譯成 (regex, include)；註解與空白行回傳 None；格式錯誤拋 ValueError。"""
    original = line
    if line.startswith("#"):  # 要比對以 # 開頭的檔名需寫成 \#
        return None
    line = _strip_trailing_spaces(line)
    if not line:
        return None
    include = True
    body = line
    if body.startswith("!"):  # 要比對以 ! 開頭的檔名需寫成 \!
        include = False
        body = body[1:]
    dir_only = body.endswith("/")
    if dir_only:
        body = body[:-1]
    if body.startswith("/"):
        body = body[1:]
        anchored = True
    else:
        anchored = "/" in body
    if not body:
        raise ValueError(f"無效的忽略規則: {original!r}")
    try:
        regex = re.compile(_body_to_regex(body, anchored, dir_only))
    except (ValueError, re.error) as e:
        raise ValueError(f"無效的忽略規則: {original!r}（{e}）") from e
    return regex, include


def _strip_trailing_spaces(line):
    # 結尾空白一律忽略，除非以反斜線跳脫（「foo\ 」保留一個空白）。
    end = len(line)
    while end > 0 and line[end - 1] == " ":
        if end > 1 and line[end - 2] == "\\":
            break
        end -= 1
    return line[:end]


def _body_to_regex(body, anchored, dir_only):
    segs = body.split("/")
    out = ["^"]
    if not anchored:
        out.append("(?:.*/)?")  # 未定錨的規則可出現在任何層級
    for i, seg in enumerate(segs):
        is_last = i == len(segs) - 1
        if seg == "":
            raise ValueError("路徑分隔符號 / 之間不可為空")
        if seg == "**":
            if is_last:
                out.append(".+")  # 結尾的 /**：該資料夾底下的所有內容
            else:
                out.append("(?:[^/]+/)*")  # 開頭 **/ 或中間 /**/：零層以上的任意資料夾
            continue
        out.append(_segment_to_regex(seg))
        if not is_last:
            out.append("/")
    # 規則比對到資料夾時，其底下所有內容也一併忽略（與 git 相同）；
    # dir_only 規則要求路徑必須是資料夾（依慣例結尾帶 /）。
    out.append("/.*" if dir_only else "(?:/.*)?")
    out.append("$")
    return "".join(out)


def _segment_to_regex(seg):
    """單一路徑段落（不含 /）的萬用字元轉譯。混在其他字元中的 ** 視為一般的 *（同 git 規格）。"""
    out = []
    i = 0
    while i < len(seg):
        c = seg[i]
        if c == "\\":
            if i + 1 >= len(seg):
                raise ValueError("反斜線後缺少被跳脫的字元")
            out.append(re.escape(seg[i + 1]))
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            negated = j < len(seg) and seg[j] in "!^"
            if negated:
                j += 1
            k = j
            if k < len(seg) and seg[k] == "]":  # 緊接在開頭的 ] 視為字面字元
                k += 1
            while k < len(seg) and seg[k] != "]":
                k += 2 if seg[k] == "\\" else 1
            if k >= len(seg):
                raise ValueError("未閉合的 [ ] 字元集合")
            out.append("[" + ("^" if negated else "") + seg[j:k] + "]")
            i = k + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)

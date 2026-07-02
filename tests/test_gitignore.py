"""gitignore.py 單元測試：gitignore 規則比對的純標準庫實作。

比對慣例：路徑相對於根目錄、以 / 分隔；資料夾在結尾加上 /。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gitignore import GitIgnoreSpec


def match(patterns, path):
    return GitIgnoreSpec.from_lines(patterns).match_file(path)


class TestBasicWildcards:
    def test_star_matches_any_filename_at_any_level(self):
        assert match(["*.tmp"], "a.tmp")
        assert match(["*.tmp"], "sub/deep/a.tmp")
        assert not match(["*.tmp"], "a.tmpx")
        assert not match(["*.tmp"], "a.txt")

    def test_star_does_not_cross_directory_separator(self):
        assert match(["sub/*.txt"], "sub/a.txt")
        assert not match(["sub/*.txt"], "sub/deep/a.txt")

    def test_question_mark_matches_single_character_only(self):
        assert match(["?.txt"], "a.txt")
        assert not match(["?.txt"], "ab.txt")

    def test_character_class_and_negated_class(self):
        assert match(["[a-c].txt"], "b.txt")
        assert not match(["[a-c].txt"], "d.txt")
        assert match(["[!a-c].txt"], "d.txt")
        assert not match(["[!a-c].txt"], "b.txt")

    def test_class_shorthand_like_pyc_pyo_pyd(self):
        assert match(["*.py[cod]"], "x.pyc")
        assert match(["*.py[cod]"], "x.pyo")
        assert not match(["*.py[cod]"], "x.py")

    def test_plain_name_matches_file_and_directory_and_contents(self):
        assert match(["build"], "build")
        assert match(["build"], "build/")
        assert match(["build"], "sub/build")
        assert match(["build"], "build/out.txt")


class TestAnchoring:
    def test_leading_slash_anchors_to_root(self):
        assert match(["/debug.txt"], "debug.txt")
        assert not match(["/debug.txt"], "sub/debug.txt")

    def test_middle_slash_anchors_to_root(self):
        assert match(["sub/a.txt"], "sub/a.txt")
        assert not match(["sub/a.txt"], "x/sub/a.txt")

    def test_no_slash_matches_at_any_level(self):
        assert match(["a.txt"], "a.txt")
        assert match(["a.txt"], "x/y/a.txt")


class TestDirectoryOnly:
    def test_trailing_slash_matches_directory_not_file(self):
        assert match(["logs/"], "logs/")
        assert not match(["logs/"], "logs")  # 同名「檔案」不算

    def test_directory_pattern_matches_at_any_level_and_contents(self):
        assert match(["logs/"], "a/logs/")
        assert match(["logs/"], "logs/x.log")


class TestDoubleStar:
    def test_leading_double_star_matches_any_depth(self):
        assert match(["**/foo"], "foo")
        assert match(["**/foo"], "x/y/foo")

    def test_trailing_double_star_matches_contents_not_directory_itself(self):
        assert match(["foo/**"], "foo/inner.txt")
        assert match(["foo/**"], "foo/sub/deep.txt")
        assert not match(["foo/**"], "foo/")  # 資料夾本身不忽略（只忽略其內容），同 git 行為

    def test_middle_double_star_matches_zero_or_more_directories(self):
        assert match(["a/**/b"], "a/b")
        assert match(["a/**/b"], "a/x/b")
        assert match(["a/**/b"], "a/x/y/b")
        assert not match(["a/**/b"], "a/b2")

    def test_double_star_mixed_with_other_characters_acts_as_single_star(self):
        assert match(["a**b"], "ab")
        assert match(["a**b"], "axyb")
        assert not match(["a**b"], "a/b")


class TestNegation:
    def test_negation_re_includes_previously_ignored_file(self):
        patterns = ["*.log", "!important.log"]
        assert match(patterns, "x.log")
        assert not match(patterns, "important.log")

    def test_last_matching_rule_wins(self):
        patterns = ["!important.log", "*.log"]  # 反向規則寫在前面會被後面的規則蓋掉
        assert match(patterns, "important.log")


class TestCommentsAndBlank:
    def test_comment_and_blank_lines_are_skipped(self):
        spec = GitIgnoreSpec.from_lines(["# 註解", "", "   ", "*.tmp"])
        assert spec.match_file("a.tmp")
        assert not spec.match_file("# 註解")

    def test_escaped_hash_matches_literal_filename(self):
        assert match(["\\#special"], "#special")

    def test_escaped_bang_matches_literal_filename(self):
        assert match(["\\!important"], "!important")

    def test_trailing_spaces_are_stripped(self):
        assert match(["*.tmp   "], "a.tmp")


class TestInvalidPatterns:
    @pytest.mark.parametrize("bad_line", ["!", "/", "!/", "a\\", "[abc", "a//b"])
    def test_invalid_pattern_raises_value_error(self, bad_line):
        with pytest.raises(ValueError):
            GitIgnoreSpec.from_lines([bad_line])

    def test_error_message_contains_original_line(self):
        with pytest.raises(ValueError, match="!"):
            GitIgnoreSpec.from_lines(["!"])


class TestEmptySpec:
    def test_no_rules_matches_nothing(self):
        spec = GitIgnoreSpec.from_lines([])
        assert not spec.match_file("anything.txt")

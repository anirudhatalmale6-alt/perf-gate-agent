"""Unit tests for the static detectors and diff-scoping logic."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from perf_gate.detectors import analyze_file
from perf_gate.detectors.base import scan_lines


def _rules(findings):
    return {f.rule_id for f in findings}


def test_java_n_plus_one_and_concat():
    src = """
public class R {
  public String go(java.util.List<Integer> ids) throws Exception {
    String out = "";
    for (Integer id : ids) {
      java.sql.Statement st = conn.createStatement();
      java.sql.ResultSet rs = st.executeQuery("SELECT * FROM t WHERE id=" + id);
      out += rs.getString("name");
    }
    return out;
  }
}
"""
    rules = _rules(analyze_file("R.java", src))
    assert "java.n_plus_one" in rules
    assert "java.string_concat_loop" in rules
    assert "java.select_star" in rules


def test_java_eager_fetch_metadata():
    src = """
@Entity class C {
  @OneToMany(fetch = FetchType.EAGER)
  private java.util.List<Object> orders;
}
"""
    assert "java.eager_fetch" in _rules(analyze_file("C.java", src))


def test_java_dcl_not_masked_by_comment():
    src = """
public class S {
  private static S instance;   // double-checked locking without volatile
  public static S getInstance() {
    if (instance == null) {
      synchronized (S.class) {
        if (instance == null) instance = new S();
      }
    }
    return instance;
  }
}
"""
    assert "java.dcl_no_volatile" in _rules(analyze_file("S.java", src))


def test_java_dcl_suppressed_when_volatile_present():
    src = """
public class S {
  private static volatile S instance;
  public static S getInstance() {
    if (instance == null) {
      synchronized (S.class) { if (instance == null) instance = new S(); }
    }
    return instance;
  }
}
"""
    assert "java.dcl_no_volatile" not in _rules(analyze_file("S.java", src))


def test_python_nested_loop_and_async():
    src = """
import time, requests
class P:
    def match(self, a, b):
        r = []
        for x in a:
            for y in b:
                if x == y:
                    r.append(x)
        return r
    async def fetch(self, ids):
        for i in ids:
            requests.get("http://x/" + str(i))
            time.sleep(0.1)
"""
    rules = _rules(analyze_file("p.py", src))
    assert "py.nested_loop" in rules
    assert "py.sync_io_in_async" in rules


def test_sql_rules():
    src = "SELECT * FROM t WHERE UPPER(name) = 'X';\nSELECT id FROM t WHERE name LIKE '%z';"
    rules = _rules(analyze_file("q.sql", src))
    assert "sql.select_star" in rules
    assert "sql.function_on_column" in rules
    assert "sql.leading_wildcard" in rules


def test_loop_depth_scanner():
    src = "for (int i=0;i<n;i++){\n  x();\n}\ny();"
    lines = scan_lines(src, "java")
    assert lines[1].loop_depth == 1   # x() is inside the loop
    assert lines[3].loop_depth == 0   # y() is outside


def test_no_false_positive_on_clean_code():
    src = """
public class Clean {
  public int sum(int[] a) {
    int s = 0;
    for (int v : a) { s += v; }
    return s;
  }
}
"""
    # s += v is an int, not a String, so no string-concat finding.
    assert "java.string_concat_loop" not in _rules(analyze_file("Clean.java", src))

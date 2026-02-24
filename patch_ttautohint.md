# ttfautohint-py _version.py パッチ

## 対象ファイル

```text
%USERPROFILE%\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\ttfautohint\_version.py
```

## 背景

`ttfautohint-py 0.5.1` は `pkg_resources` (setuptools が提供) を使ってバージョン情報を取得している。
`setuptools 71.0.0` 以降で `pkg_resources` が削除されたため、Python 3.14 + setuptools 82.0.0 環境では import 時にエラーが発生する。

加えて、`_version.py` の `except (ImportError, DistributionNotFound)` という書き方に問題がある。
`from pkg_resources import ... DistributionNotFound` が `ModuleNotFoundError` (ImportError のサブクラス) で失敗した場合、
`except` 節で `DistributionNotFound` が未定義のまま評価され `NameError` になる。

```text
ModuleNotFoundError: No module named 'pkg_resources'
→ NameError: name 'DistributionNotFound' is not defined
```

## パッチ内容

`pkg_resources` の代わりに Python 3.8 以降の標準ライブラリ `importlib.metadata` を使用する。

```diff
-try:
-    from pkg_resources import get_distribution, DistributionNotFound
-    __version__ = get_distribution("ttfautohint-py").version
-except (ImportError, DistributionNotFound):
-    # either pkg_resources is missing or package is not installed
-    import warnings
-    warnings.warn(
-        "'ttfautohint-py' is missing the required distribution metadata. "
-        "Please make sure it was installed correctly.", UserWarning,
-        stacklevel=2)
-    __version__ = "0.0.0"
+try:
+    from importlib.metadata import version, PackageNotFoundError
+    __version__ = version("ttfautohint-py")
+except (ImportError, PackageNotFoundError):
+    # either importlib.metadata is missing or package is not installed
+    import warnings
+    warnings.warn(
+        "'ttfautohint-py' is missing the required distribution metadata. "
+        "Please make sure it was installed correctly.", UserWarning,
+        stacklevel=2)
+    __version__ = "0.0.0"
```

## 確認

```text
$ python3 -c "from ttfautohint import ttfautohint; print('ttfautohint OK')"
ttfautohint OK
```

## 備考

環境を再構築した際やパッケージを更新した際は再度パッチが必要になる。
`ttfautohint-py` の上流リポジトリへの issue 報告または PR 送付を検討すること。

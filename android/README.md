# 四级背单词 · 安卓工程

一个「WebView 外壳 + 离线网页」的最小安卓 App：不用 Gradle、不用联网，
用系统里已有的 Android SDK 直接编译出签名 APK。

## 目录

```
android/
  AndroidManifest.xml                     应用清单（不申请任何权限）
  src/com/cet4/words/MainActivity.java    WebView 外壳 + 存储桥 + TTS + 文件读写
  res/                                    图标、主题、颜色
  assets/index.html                       由 tools/build.py 生成的词库页面
  build.ps1                               一键编译脚本（自动找 SDK / JDK / Python）
  pack.py                                 把 aapt2 的 base.apk 与 classes.dex、assets 合成
  cet4.keystore                           签名密钥（勿删，删了就不能覆盖安装）
```

## 编译

```powershell
# 词库或页面有改动时，先生成网页（会同时写入 android/assets/index.html）
python tools/build.py

# 再打包 APK
powershell -NoProfile -ExecutionPolicy Bypass -File android\build.ps1
```

产物：仓库根目录的 `四级背单词.apk`（约 150 KB，minSdk 23 / targetSdk 34）。

依赖：Android SDK（build-tools + platforms/android-34）、JDK 17+、Python 3。
脚本会自动在 `%LOCALAPPDATA%\Android\Sdk`、`%USERPROFILE%\Android\Sdk` 里找 SDK，
在 `JAVA_HOME` 与 `C:\Program Files\Java` 等常见位置找 JDK；找不到就报错让你设环境变量。

## 实现要点

- **完全离线**：APK 不含 INTERNET 权限，词库和界面都在 assets 里。
- **记录持久化**：优先用 WebView 的 localStorage；若被系统禁用，自动改用原生
  SharedPreferences（`AndroidBridge.getPref/setPref`），换启动不丢。
- **朗读**：优先走安卓系统 TTS（`AndroidBridge.speak`），没有 TTS 引擎时无声。
- **返回键**：不退出应用，只退到后台，防止误触丢失一轮答题。
- **存档读写**：导入用 `onShowFileChooser`，导出用 `ACTION_CREATE_DOCUMENT`
  系统保存对话框（SAF，不需要存储权限），写完回调网页 `window.__onSaveResult(ok)`。
- **签名**：固定密钥 `cet4.keystore`（口令 cet4pass）。换密钥必须先卸载旧版才能安装。

## 记录口径

- **背会**：某个词「最近一次作答」答对即算背会；之后答错会退回未背会。
- **课程进度**：每课显示「已背会 / 该课总词数 · 该课正确率」，没做过显示“未测”。
- **错词本**：答错过的词**永久保留**，不提供手动/自动移出；行内标「已背会」表示该词后来答对过。
- **错词本筛选**：先点 Unit 再点 Lesson，只看这一课；两个都不选＝全部。单词行始终标注来源 U/L。
- **错题专练**：练整个错词本（数量见按钮）。

## 存档格式

导出的 `.json` 是纯数据快照（不含词库）：

```json
{
  "app": "cet4-words", "format": 2, "exportedAt": "2026-11-03T13:18:07.512Z",
  "summary": { "words": 126, "attempts": 431, "wrong": 38, "mastered": 97, "favorites": 14 },
  "stats": { "word": { "seen": 3, "wrong": 1, "right": 2, "ok": 1, "last": 1699000000000 } },
  "favorites": { "word": 1699000000000 },
  "setup": { "lessons": ["1-1", "1-2"], "count": 20, "pick": "random" }
}
```

导入时可选「合并」（次数相加、较新的状态优先、收藏取并集）或「覆盖」（整份替换）。
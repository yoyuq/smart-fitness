# 静态接口清单检查使用指南

`scripts/workflow_contract/check_contracts.py` 用于离线比较 Android Retrofit 声明与后端声明的 HTTP 方法、路径。它只依赖 Python 标准库，不安装依赖、不访问在线服务，也不导入或执行应用模块。静态清单匹配不能证明运行时 API 兼容性。

## 运行方式

使用 Python 3.10 或更新版本，在仓库根目录执行：

```sh
python -B scripts/workflow_contract/check_contracts.py --repo .
python -B scripts/workflow_contract/check_contracts.py --repo . --strict
```

`--repo` 必填，指向待扫描仓库根目录；可使用带引号的绝对路径。脚本路径仍须相对于当前目录或写为绝对路径。`-B` 避免生成 Python 字节码缓存。

本次 Windows PowerShell 验证使用控制器提供的解释器：

```powershell
& 'C:\Users\hjl\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B scripts/workflow_contract/check_contracts.py --repo .
$LASTEXITCODE
& 'C:\Users\hjl\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B scripts/workflow_contract/check_contracts.py --repo . --strict
$LASTEXITCODE
```

成功完成扫描时，标准输出是缩进的 JSON。默认模式允许存在未匹配项；`--strict` 只改变未匹配时的退出码，不改变 JSON 内容。

| 情况 | 默认退出码 | `--strict` 退出码 |
| --- | --- | --- |
| 扫描完成且 `missing` 为空 | 0 | 0 |
| 扫描完成且 `missing` 非空 | 0 | 1 |
| 扫描异常，如目录/输入缺失、读取失败或 Python 源文件语法错误 | 2 | 2 |

扫描异常会在标准错误输出 `Contract audit failed: ...`，不输出成功结果 JSON。命令行参数错误由 argparse 报告，退出码也是 2。严格模式退出 0 只说明这份静态清单没有未匹配项；空清单也可能退出 0，不能据此认定接口完整。

## 输出字段与比较规则

| 字段 | 含义 |
| --- | --- |
| `backend_count` | 后端提取记录数，包含不同文件或位置的重复声明，不是运行中的唯一接口数。 |
| `android_count` | Android 提取记录数，同样按声明记录计数。 |
| `matched` | 在后端清单找到相同方法与规范化路径的 Android 记录数组，不是数量，也不是后端记录数组。 |
| `missing` | 在后端清单找不到对应项的 Android 记录数组，不能直接理解为后端未实现。 |
| `limitations` | 工具固定输出的限制说明字符串数组；不是逐文件诊断，也不统计被跳过的声明。 |

`matched` 与 `missing` 中每条记录恰好包含四个字段：`method` 是大写 HTTP 方法；`path` 以 `/` 开头并保留原参数名称；`source` 是相对于仓库的 POSIX 风格路径；`line` 是从 1 开始的注解/装饰器行号。两个提取器均暴露 `extract_routes(root: Path) -> list[dict]`，按 method、path、source、line 确定性排序。

比较键由 HTTP 方法和路径组成，仅在比较时将 `{参数名}` 替换为 `{}`。例如 `/items/{id}` 与 `/items/{item_id}` 可以匹配，但 GET 与 POST 不匹配，尾部斜杠及其他字面路径差异仍保留。输出记录不会被改写成规范化路径。后端比较键集合用于查找，原始计数不去重；Android 重复声明也逐条划入数组。因此 `len(matched) + len(missing) == android_count`。此检查是从 Android 到后端的单向比较，不报告后端独有接口。

## 当前仓库实测结果

2026-10-09，在基线 `e51bdfc79b3dc946bf92d6e5fc3240bac53b0ac9` 的任务工作区运行两种模式，得到 `backend_count = 103`、`android_count = 61`、`len(matched) = 58`、`len(missing) = 3`。默认退出码为 0，`--strict` 退出码为 1；两者均无标准错误输出。计数是该版本的静态快照，源文件变化后应重新运行。

三个未匹配项的 `source` 均为 `android_app/app/src/main/java/com/smartfitness/app/api/ApiService.kt`：

| 方法 | 路径 | Android 注解行 | 后端注册位置 |
| --- | --- | --- | --- |
| GET | `/api/v2/auth/profile` | 16 | `backend/main_v2_routes.py:683` |
| POST | `/api/v2/auth/login` | 13 | `backend/main_v2_routes.py:682` |
| POST | `/api/v2/auth/register` | 10 | `backend/main_v2_routes.py:681` |

检查源码可见，这三个路径在后端以 `app.add_api_route(...)` 形式注册，方法分别为 GET、POST、POST。现有后端提取器只收集指定装饰器，不识别 `add_api_route`，所以这些声明没有进入后端清单。它们是已知的扫描覆盖缺口，不能将三条 `missing` 直接认定为运行时接口缺失；同样，看到注册源码也不能证明某部署已执行注册。`backend/main_v2_extra.py:1182` 还有设备接口的 `add_api_route` 调用，说明该限制并非仅影响认证接口。

## 覆盖范围与限制

后端实现见 `scripts/workflow_contract/backend_routes.py`：递归扫描 `backend/` 下的 `main*.py` 与 `routes*.py`，通过 AST 提取字面字符串路径的 `@app.get/post/put/patch/delete/head/options` 装饰器，支持位置参数及 `path=`。不解析动态路径、别名、路由器、`add_api_route` 注册、路由替换或运行时条件。不同后端入口文件中的声明会合并统计，不证明这些声明实际同时生效。

Android 实现见 `scripts/workflow_contract/android_routes.py`：仅扫描上述 `ApiService.kt` 的字面字符串 Retrofit HTTP 动词注解，支持普通注解及 `retrofit2.http.` 限定形式，跳过注释。不支持常量路径、字符串插值、`@HTTP`、`@Url` 或其他文件中的动态声明。

比较逻辑见 `scripts/workflow_contract/check_contracts.py`。它不验证 base URL、部署前缀、参数类型、查询参数、请求体结构、认证行为、响应结构或状态码，也不启动服务器发起请求。58 条匹配只是方法/路径的静态对应，不能证明运行时 API 兼容性；部署入口、动态注册和实际请求/响应行为仍需另行验证。

## 被忽略文档的交付注意事项

已检查 `.gitignore:109` 的 `docs/` 规则，它会忽略本文件；可用 `git check-ignore -v docs/workflow-contract-check.md` 复核。任务卡中的历史遗漏经验在本工作区有该规则作为证据：文件存在或普通 `git status` 没有显示变化，都不等于文档已进入提交。

本任务只交付明确列入范围的 `docs/workflow-contract-check.md`，不修改代码或 `.gitignore`。由控制器处理该单文件的提交，并在全新已提交的审计工作区独立确认文件存在且被 Git 跟踪；不要强制加入整个 `docs/` 目录。本地文档检查通过不能替代控制器的提交与最终验收。

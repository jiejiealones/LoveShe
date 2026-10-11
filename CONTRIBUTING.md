# 参与开发

需要 Python 3.11+。在源码根目录运行：

```powershell
python -m unittest discover -s tests -v
python scripts/check_release.py
node --check loveshe/static/app.js
python -m pip install detect-secrets==1.5.0
python scripts/scan_secrets.py
```

Node 只用于前端语法检查；手动 JSON 路径不依赖 Node。所有测试使用虚构数据和模拟模型，不应连接微信、调用付费模型或读取开发者的私人数据。

提交时说明改动、验证方式和限制。不要提交 `data/`、`private/`、模型配置、截图中的个人信息或真实导出。新增示例必须从零编造，不能只替换姓名。导入、记忆隔离、纠错、删除、授权范围与错误恢复的改动应有针对性测试。

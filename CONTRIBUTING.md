# Contributing

感谢参与这个实验项目。提交改动前，请先阅读 README 中的非官方接口声明和
`docs/SECURITY_MODEL.md`。

## 开发环境

```bash
git clone https://github.com/gitliu-my/codex_subscription_client.git
cd codex_subscription_client
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

## 提交原则

- 保持 Python 3.10+ 兼容，不要无必要增加运行时依赖。
- 新行为和错误修复应带有聚焦的单元测试。
- 不要提交 token、账号 ID、API Key、个人路径或真实请求日志。
- 修改 OAuth、CORS、API 鉴权、管理页会话或凭据存储时，要同步更新安全测试和文档。
- 不要将本地服务改为默认监听局域网地址。

安全漏洞不要通过公开 Issue 或 Pull Request 首次披露，请遵循 `SECURITY.md`。

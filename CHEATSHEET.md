# 采购系统 - 架构速查卡

## 🎯 一句话概述

意大利门店采购订单自动化处理系统，通过Excel COM实现公式计算，自动选择最优供应商。

---

## 📁 文件结构

```
procurement_system/
├── main.py              # Tkinter GUI入口
├── service.py           # 业务逻辑（采购流程编排）
├── utils.py             # 工具函数（Excel COM、EAN清洗）
├── config.py            # 配置常量
├── ARCHITECTURE.md      # 架构设计文档
├── ADR.md               # 架构决策记录
├── README.md            # 使用说明
├── requirements.txt     # Python依赖
├── logs/                # 日志目录
└── tests/               # 测试目录
```

---

## 🔑 核心业务流程

```
ORDINE文件夹 → 读取主订单
    ↓
eliminare文件夹 → 逐行扣减COLLI数量
    ↓
distribuire文件夹 → 构建供应商价格矩阵
    ↓
选择最低价供应商 → 写入主表第30列
    ↓
回写订单数量 → 更新供应商文件
```

---

## 🧩 关键配置（config.py）

| 配置项 | 值 | 说明 |
|--------|------|------|
| `SUPPLIER_MATRIX_START_COL` | 31 | 价格矩阵起始列 |
| `SUPPLIER_RESULT_COL` | 30 | 供应商选择结果列 |
| `MAX_ROWS` | 10000 | Excel最大行数保护 |
| `COL_CODICE_EAN` | "CODICE EAN" | EAN列名 |
| `COL_COLLI` | "COLLI" | 数量列名 |
| `COL_PREZZO_NETTO` | "PREZZO NETTO" | 价格列名 |
| `COL_ORDINE` | "ORDINE" | 订单列名 |

---

## 🐛 已知问题

| 问题 | 严重级 | 状态 | 修复版本 |
|------|--------|------|----------|
| EAN索引删除后失效 | 高 | ✅ 已修复 | v2.0.1 |
| 线程状态硬编码延迟 | 中 | ✅ 已修复 | v2.0.1 |
| 文件扩展名大小写不一致 | 低 | ✅ 已修复 | v2.0.1 |
| Excel COM对象泄漏 | 中 | ⚠️ 部分修复 | v2.0 |

---

## 🔧 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行GUI
python main.py

# 检查Excel文件结构
python check_file.py

# 检查主表数据
python check_main.py
```

---

## 📊 性能指标

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| EAN匹配复杂度 | O(n) | O(1) |
| COM对象复用 | 无 | 连接池 |
| 并发处理 | 单线程 | 多线程 |
| 测试覆盖率 | 0% | 80% |

---

## 🚨 故障排查

### Excel进程残留

**症状**: 任务管理器中多个EXCEL.EXE进程

**原因**: COM对象未正确释放

**解决**: 
```python
# 确保调用
excel.Quit()
del excel
gc.collect()
```

### EAN匹配失败

**症状**: 价格矩阵命中数为0

**原因**: 
- EAN格式不一致（前导零、空格）
- 列名不匹配

**解决**: 检查`clean_ean()`函数逻辑

### 按钮无法点击

**症状**: "开始处理"按钮灰色

**原因**: 线程未完成

**解决**: 等待处理完成或重启应用

---

## 📞 架构决策速查

| 决策 | 理由 | 替代方案 |
|------|------|----------|
| 单体架构 | 团队小、需求稳定 | 微服务（过早抽象） |
| Excel COM | 公式计算准确 | openpyxl（不支持公式） |
| Tkinter GUI | 无需额外依赖 | PyQt（重量级） |
| 轮询线程状态 | 简单可靠 | Event信号量（复杂） |

---

## 🎓 学习资源

- **DDD**: 《实现领域驱动设计》
- **架构**: 《Clean Architecture》
- **Python**: 《Fluent Python》
- **测试**: pytest官方文档

---

*最后更新: 2026-04-26*  
*版本: v2.0.1*

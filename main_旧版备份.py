import threading
import os
import logging
from datetime import datetime
from tkinter import *
from tkinter import filedialog, messagebox, ttk
from tkinter.ttk import Separator

from service import run_process
from matcher import find_missing_items
from config import (
    ORDINE_FOLDER, ELIMINARE_FOLDER, DISTRIBUTUIRE_FOLDER,
    LOG_DIR, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT
)

# =====================
# 日志系统（UI + 文件双写）
# =====================
def _setup_file_logger():
    """设置文件日志记录器"""
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, LOG_FILE)

    file_logger = logging.getLogger("procurement")
    file_logger.setLevel(logging.INFO)
    file_logger.handlers.clear()

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    file_logger.addHandler(handler)

    return file_logger

file_logger = _setup_file_logger()


def log(msg):
    """同时写入 UI 和日志文件"""
    # 写入文件
    file_logger.info(msg)
    # 写入 UI（线程安全）
    def write():
        text.insert(END, msg + "\n")
        text.see(END)
        # 更新状态栏（只显示最后一行消息）
        if msg.strip():
            status_var.set(f"状态: {msg.strip()[:60]}")
    root.after(0, write)


# =====================
# 选择总目录
# =====================
def choose_root(entry):
    path = filedialog.askdirectory(title="选择采购系统总文件夹")
    if path:
        entry.delete(0, END)
        entry.insert(0, path)


# =====================
# 验证文件夹结构
# =====================
def validate_folder(root_path):
    """验证文件夹是否包含必要的子文件夹"""
    errors = []

    ordine_path = os.path.join(root_path, ORDINE_FOLDER)
    eliminare_path = os.path.join(root_path, ELIMINARE_FOLDER)
    distribuire_path = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)

    if not os.path.isdir(ordine_path):
        errors.append(f"缺少文件夹: {ORDINE_FOLDER}")
    elif not any(f.lower().endswith((".xls", ".xlsx")) for f in os.listdir(ordine_path)):
        errors.append(f"{ORDINE_FOLDER} 文件夹内没有 Excel 文件")

    if not os.path.isdir(eliminare_path):
        errors.append(f"缺少文件夹: {ELIMINARE_FOLDER}")

    if not os.path.isdir(distribuire_path):
        errors.append(f"缺少文件夹: {DISTRIBUTUIRE_FOLDER}")
    elif not any(f.lower().endswith((".xls", ".xlsx")) for f in os.listdir(distribuire_path)):
        errors.append(f"{DISTRIBUTUIRE_FOLDER} 文件夹内没有 Excel 文件")

    return errors


# =====================
# 主逻辑入口
# =====================
def run_safe():
    try:
        root_path = entry_root.get().strip()

        if not root_path:
            raise ValueError("请选择总文件夹")

        if not os.path.isdir(root_path):
            raise ValueError(f"文件夹不存在: {root_path}")

        # 验证文件夹结构
        errors = validate_folder(root_path)
        if errors:
            raise ValueError("文件夹结构验证失败:\n" + "\n".join(errors))

        ordine_path = os.path.join(root_path, ORDINE_FOLDER)
        eliminare_path = os.path.join(root_path, ELIMINARE_FOLDER)
        distribuire_path = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)

        # 找 ORDINE 文件
        ordine_files = sorted([
            f for f in os.listdir(ordine_path)
            if f.lower().endswith((".xls", ".xlsx"))
        ])
        ordine_file = os.path.join(ordine_path, ordine_files[0])

        log(f"\n{'='*50}")
        log(f"总文件夹: {root_path}")
        log(f"主文件: {ordine_file}")
        log(f"{'='*50}\n")

        run_process(
            ordine_file,
            eliminare_path,
            distribuire_path,
            log
        )

        log("\n✔ 已自动保存（覆盖原文件）")

    except ValueError as e:
        err = str(e)
        file_logger.error(err)
        root.after(0, lambda: messagebox.showerror("验证错误", err))
    except Exception as e:
        err = f"处理失败: {e}"
        file_logger.error(err, exc_info=True)
        root.after(0, lambda: messagebox.showerror("错误", err))


# =====================
# 启动线程
# =====================
def start():
    btn_start.config(state=DISABLED, text="处理中...")
    thread = threading.Thread(target=run_safe, daemon=True)
    thread.start()
    
    # 正确的方式：轮询检查线程状态
    def check_done():
        if thread.is_alive():
            # 线程还在运行，1秒后再次检查
            root.after(1000, check_done)
        else:
            # 线程已完成，恢复按钮
            btn_start.config(state=NORMAL, text="开始处理")
    
    # 开始检查
    root.after(1000, check_done)


# =====================
# 缺失查找功能
# =====================
def run_web_search():
    """对选中的行进行网络搜索"""
    from matcher import search_web_ean
    
    selected = tree.selection()
    if not selected:
        messagebox.showinfo("提示", "请先选择一行")
        return
    
    item = tree.item(selected[0])
    values = item["values"]
    ean = values[0]
    articolo = values[1]
    
    if not ean:
        messagebox.showinfo("提示", "没有 EAN 码")
        return
    
    btn_web.config(state=DISABLED, text="搜索中...")
    
    def search_thread():
        try:
            log(f"\n{'='*60}")
            log(f"开始网络搜索")
            log(f"{'='*60}")
            log(f"搜索 EAN: {ean}")
            log(f"产品: {articolo}")
            log("正在搜索...")
            
            # 调用网络搜索（传递 log 作为日志回调）
            result, source, search_time = search_web_ean(ean, articolo, log)
            
            if result:
                log(f"\n搜索结果:")
                log(f"  产品: {result}")
                log(f"  来源: {source}")
                log(f"  时间: {search_time}")
                messagebox.showinfo("搜索结果", f"产品信息:\n{result}\n\n来源: {source}\n时间: {search_time}")
            else:
                log(f"\n未找到搜索结果: {source}")
                messagebox.showinfo("搜索结果", f"未找到 EAN {ean} 的相关信息")
                
        except Exception as e:
            log(f"搜索失败: {e}")
            messagebox.showerror("错误", f"搜索失败: {e}")
        finally:
            root.after(0, lambda: btn_web.config(state=NORMAL, text="🌐 网络搜索"))
    
    thread = threading.Thread(target=search_thread, daemon=True)
    thread.start()


def run_missing_search():
    """在后台线程运行缺失查找"""
    btn_search.config(state=DISABLED, text="查找中...")
    root.after(0, lambda: status_var.set("状态: 开始缺失查找..."))
    
    def search_thread():
        try:
            root_path = entry_root.get().strip()
            if not root_path or not os.path.isdir(root_path):
                root.after(0, lambda: messagebox.showerror("错误", "请先选择总文件夹"))
                return
            
            distribuire_path = os.path.join(root_path, DISTRIBUTUIRE_FOLDER)
            if not os.path.isdir(distribuire_path):
                root.after(0, lambda: messagebox.showerror("错误", f"缺少 {DISTRIBUTUIRE_FOLDER} 文件夹"))
                return
            
            log(f"\n{'='*50}")
            log(f"开始缺失查找")
            log(f"{'='*50}\n")
            
            # 获取阈值
            threshold = float(entry_threshold.get())
            use_web = var_use_web.get()
            
            if use_web:
                log("使用网络搜索辅助...")
                log("提示: 搜索完成后会弹出提示，请点击右上角 '💾 导出结果' 保存\n")
            
            # 传递 log 作为日志回调
            results, error = find_missing_items(distribuire_path, threshold, use_web, log)
            
            if error:
                log(f"❌ {error}")
                return
            
            if not results:
                log("未找到缺失项目")
                return
            
            log(f"缺失项目总数: {len(results)}\n")
            
            # 清空并填充结果表格
            for item in tree.get_children():
                tree.delete(item)
            
            found_count = 0
            web_found_count = 0
            for ean, articolo, count, matches, web_source, web_art, search_time in results:
                if not matches:
                    # 没有匹配
                    tree.insert("", END, values=(
                        ean,
                        articolo[:50] + "..." if len(articolo) > 50 else articolo,
                        count or "",
                        "未找到",
                        "",
                        web_art[:40] if web_art else "无",
                        search_time or "",
                        ""
                    ))
                else:
                    # 取最佳匹配 (total_sim, ean, art, supplier, art_sim, web_art)
                    best_total, best_ean, best_art, best_supplier, best_art_sim, best_web_art = matches[0]
                    found_count += 1
                    web_info = best_web_art[:40] if best_web_art else (web_art[:40] if web_art else "无")
                    source_info = f"\n来源: {web_source[:40]}" if web_source and 'http' in str(web_source) else ""
                    tree.insert("", END, values=(
                        ean,
                        articolo[:50] + "..." if len(articolo) > 50 else articolo,
                        count or "",
                        f"{best_total:.0%}",
                        f"{best_art_sim:.0%}",
                        web_info,
                        search_time or "",
                        f"EAN: {best_ean}\nART: {best_art[:30]}\n供应商: {best_supplier}{source_info}"
                    ))
                    if web_source:
                        web_found_count += 1
            
            log(f"\n{'='*50}")
            log(f"[完成] 查找完成")
            log(f"本地匹配: {found_count}/{len(results)}")
            if use_web and web_found_count:
                log(f"网络匹配: {web_found_count}")
            log(f"{'='*50}\n")
            
            # 显示完成提示
            completion_msg = f"查找完成！\n\n本地匹配: {found_count}/{len(results)}"
            if use_web and web_found_count:
                completion_msg += f"\n网络匹配: {web_found_count}"
            completion_msg += "\n\n请点击右上角 '💾 导出结果' 按钮保存"
            
            root.after(0, lambda: messagebox.showinfo("搜索完成", completion_msg))
            
        except Exception as e:
            error_msg = str(e)
            log(f"❌ 查找失败: {error_msg}")
            root.after(0, lambda msg=error_msg: messagebox.showerror("错误", f"查找失败: {msg}"))
        finally:
            root.after(0, lambda: btn_search.config(state=NORMAL, text="🔍 缺失查找"))
    
    thread = threading.Thread(target=search_thread, daemon=True)
    thread.start()


def clean_ean_for_export(ean):
    """清理 EAN 码，确保是整数格式"""
    if not ean:
        return ""
    # 去除小数点和前导零
    ean_str = str(ean).replace('.', '').replace(',', '')
    # 转换为整数再转字符串（去除前导零）
    try:
        return str(int(ean_str))
    except:
        return ean_str


def export_search_results():
    """导出查找结果到 Excel"""
    try:
        from openpyxl import Workbook
        
        wb = Workbook()
        ws = wb.active
        ws.title = "缺失查找结果"
        
        # 表头
        ws.append(["缺失 EAN", "缺失 ARTICOLO", "数量", "综合匹配", "ART匹配", "网络搜索", "搜索时间", "匹配的 EAN", "匹配的 ARTICOLO", "供应商"])
        
        # 数据
        for item in tree.get_children():
            values = tree.item(item)["values"]
            ean, articolo, count, total_sim, art_sim, web_art, search_time, detail = values
            
            # 清理 EAN 码（去除小数点）
            clean_ean = clean_ean_for_export(ean)
            
            if total_sim == "未找到":
                ws.append([clean_ean, articolo, count, "", "", web_art, search_time, "", "", ""])
            else:
                # 解析详情
                parts = detail.split('\n')
                found_ean = clean_ean_for_export(parts[0].replace("EAN: ", "")) if len(parts) > 0 else ""
                found_art = parts[1].replace("ART: ", "") if len(parts) > 1 else ""
                supplier = parts[2].replace("供应商: ", "") if len(parts) > 2 else ""
                ws.append([clean_ean, articolo, count, total_sim, art_sim, web_art, search_time, found_ean, found_art, supplier])
        
        # 自动调整列宽
        for col in ws.columns:
            max_length = 0
            column_letter = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            ws.column_dimensions[column_letter].width = min(max_length + 2, 50)
        
        # 保存
        file_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
            initialfile=f"缺失查找结果_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        )
        
        if file_path:
            wb.save(file_path)
            log(f"✔ 结果已导出到: {file_path}")
            messagebox.showinfo("成功", f"结果已导出到:\n{file_path}")
        
        wb.close()
        
    except Exception as e:
        messagebox.showerror("导出失败", str(e))


# =====================
# UI
# =====================
root = Tk()
root.title("采购系统 v2.5.0 - AI优化版")
root.geometry("950x700")  # 增加窗口高度以显示状态栏
root.minsize(850, 550)

# 顶部框架
top_frame = Frame(root, padx=20, pady=10)
top_frame.pack(fill=X)

Label(
    top_frame,
    text="选择总文件夹（包含 ORDINE / eliminare / distribuire）",
    font=("Microsoft YaHei", 10)
).pack(anchor=W)

entry_frame = Frame(top_frame)
entry_frame.pack(fill=X, pady=5)

entry_root = Entry(entry_frame, width=80, font=("Consolas", 10))
entry_root.pack(side=LEFT, fill=X, expand=True)

btn_choose = Button(
    entry_frame,
    text="📁 选择文件夹",
    command=lambda: choose_root(entry_root),
    padx=10
)
btn_choose.pack(side=RIGHT, padx=5)

# 按钮框架
btn_frame = Frame(top_frame)
btn_frame.pack(fill=X, pady=5)

btn_start = Button(
    btn_frame,
    text="🚀 开始处理",
    command=start,
    bg="#4CAF50",
    fg="white",
    font=("Microsoft YaHei", 11, "bold"),
    padx=20,
    pady=8
)
btn_start.pack(side=LEFT, padx=5)

# 缺失查找按钮和阈值设置
search_frame = Frame(btn_frame)
search_frame.pack(side=LEFT, padx=10)

Label(
    search_frame,
    text="匹配阈值:",
    font=("Microsoft YaHei", 9)
).pack(side=LEFT, padx=5)

entry_threshold = Entry(search_frame, width=6, font=("Consolas", 9), justify="center")
entry_threshold.insert(0, "0.6")
entry_threshold.pack(side=LEFT, padx=5)

var_use_web = BooleanVar(value=False)
Checkbutton(
    search_frame,
    text="网络搜索",
    variable=var_use_web,
    font=("Microsoft YaHei", 9)
).pack(side=LEFT, padx=5)

btn_search = Button(
    search_frame,
    text="🔍 缺失查找",
    command=run_missing_search,
    bg="#2196F3",
    fg="white",
    font=("Microsoft YaHei", 10, "bold"),
    padx=15,
    pady=5
)
btn_search.pack(side=LEFT, padx=5)

btn_web = Button(
    search_frame,
    text="🌐 网络搜索",
    command=run_web_search,
    bg="#9C27B0",
    fg="white",
    font=("Microsoft YaHei", 9),
    padx=10,
    pady=3
)
btn_web.pack(side=LEFT, padx=5)

btn_export = Button(
    btn_frame,
    text="💾 导出结果",
    command=export_search_results,
    bg="#FF5722",  # 更醒目的颜色
    fg="white",
    font=("Microsoft YaHei", 11, "bold"),  # 加粗字体
    padx=20,
    pady=8
)
btn_export.pack(side=RIGHT, padx=5)

# 状态栏
status_var = StringVar()
status_var.set("就绪 - 先选择文件夹，然后点击 '开始处理' 或 '缺失查找'")
status_bar = Label(
    root,
    textvariable=status_var,
    font=("Microsoft YaHei", 9),
    fg="#666",
    anchor=W,
    padx=20,
    pady=5
)
status_bar.pack(fill=X)

# 分割线
Separator(root, orient=HORIZONTAL).pack(fill=X, padx=20)

# 主内容区域 - 左右分栏
main_frame = Frame(root, padx=20, pady=10)
main_frame.pack(fill=BOTH, expand=True)

# 左侧：日志
log_frame = Frame(main_frame, width=300)
log_frame.pack(side=LEFT, fill=BOTH, padx=(0, 10))
log_frame.pack_propagate(False)

Label(
    log_frame,
    text="📋 处理日志",
    font=("Microsoft YaHei", 9, "bold")
).pack(anchor=W)

text = Text(
    log_frame,
    height=20,
    font=("Consolas", 9),
    wrap=WORD
)
text.pack(fill=BOTH, expand=True, pady=5)

# 右侧：缺失查找结果
result_frame = Frame(main_frame)
result_frame.pack(side=RIGHT, fill=BOTH, expand=True)

Label(
    result_frame,
    text="📊 缺失查找结果",
    font=("Microsoft YaHei", 9, "bold")
).pack(anchor=W)

          # 创建表格
columns = ("EAN", "ARTICOLO", "数量", "综合", "ART", "网络条码", "时间", "匹配详情")
tree = ttk.Treeview(result_frame, columns=columns, show="headings", height=18)

# 设置列头和宽度
tree.heading("EAN", text="EAN")
tree.column("EAN", width=120)
tree.heading("ARTICOLO", text="ARTICOLO")
tree.column("ARTICOLO", width=140)
tree.heading("数量", text="数量")
tree.column("数量", width=40)
tree.heading("综合", text="综合匹配")
tree.column("综合", width=60)
tree.heading("ART", text="ART匹配")
tree.column("ART", width=60)
tree.heading("网络条码", text="网络搜索结果")
tree.column("网络条码", width=150)
tree.heading("时间", text="搜索时间")
tree.column("时间", width=60)
tree.heading("匹配详情", text="匹配详情")
tree.column("匹配详情", width=180)

tree.pack(fill=BOTH, expand=True, pady=5)

# 底部状态栏
status_frame = Frame(root, padx=20, pady=5)
status_frame.pack(fill=X)
Label(
    status_frame,
    text=f"日志文件: {os.path.join(LOG_DIR, LOG_FILE)}",
    font=("Microsoft YaHei", 8),
    fg="gray"
).pack(anchor=W)

log(f"采购系统启动 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log("提示: 先运行 '开始处理' 生成缺失报告，再点击 '缺失查找'")
log("新功能: 自动网络搜索 + 三 ARTICOLO 匹配，显示搜索时间和条码信息")

root.mainloop()
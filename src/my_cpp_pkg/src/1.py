import os
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
import statsmodels.api as sm
from statsmodels.stats.multitest import fdrcorrection
import matplotlib.pyplot as plt
import seaborn as sns

# ======================================================
# 路径设置
# ======================================================
# 根据当前脚本所在目录构建路径，避免硬编码盘符
HERE = os.path.dirname(os.path.abspath(__file__))
# 允许通过环境变量 BC_BASE_DIR 覆盖默认路径，方便在不同机器上运行
BASE_DIR = os.environ.get(
    "BC_BASE_DIR",
    os.path.join(HERE, "..", "..", "..", "..", "Gretna_results", "wei_AHC_paired", "BetweennessCentrality")
)
GROUP1_DIR = os.path.join(BASE_DIR, "Group1")   # Pre
GROUP2_DIR = os.path.join(BASE_DIR, "Group2")   # HC
OUT_DIR = os.path.join(BASE_DIR, "PythonStats")
os.makedirs(OUT_DIR, exist_ok=True)

metric = "aBc.txt"   # 节点指标文件
metric_name = os.path.splitext(metric)[0]
NODE_NAME_FILE = os.path.join(HERE, "3net_nodename.txt")
NETWORK_LABEL_FILE = os.path.join(HERE, "3net_networkindex.txt")
COVARIATE_FILE = os.path.join(HERE, "subjects_info_preHC.csv")

# ======================================================
# 读取指标数据
# ======================================================
g1 = np.loadtxt(os.path.join(GROUP1_DIR, metric))
g2 = np.loadtxt(os.path.join(GROUP2_DIR, metric))

# 读取协变量（独立组，不用 ID 匹配），只取前四列避免尾随空列
cov_df = pd.read_csv(COVARIATE_FILE, usecols=["Group", "Age", "Sex", "HeadMotion"])
cov_df = cov_df.dropna(subset=["Group", "Age", "Sex", "HeadMotion"])
cov_pre = cov_df[cov_df["Group"].str.lower()=="pre"].reset_index(drop=True)
cov_hc = cov_df[cov_df["Group"].str.lower()=="hc"].reset_index(drop=True)

# 基本校验，避免空组导致后续模型报错
if len(cov_pre)==0 or len(cov_hc)==0:
    raise ValueError(f"协变量文件中 Pre 组 {len(cov_pre)} 个，HC 组 {len(cov_hc)} 个，请检查 subjects_info_preHC.csv")

# 校验样本数
assert g1.shape[0] == len(cov_pre), f"Group1 样本数不匹配: g1={g1.shape[0]}, cov_pre={len(cov_pre)}"
assert g2.shape[0] == len(cov_hc), f"Group2 样本数不匹配: g2={g2.shape[0]}, cov_hc={len(cov_hc)}"

# ======================================================
# 拼接协变量
# ======================================================
y_values = np.concatenate([g1, g2])
group_values = np.array([0]*len(g1) + [1]*len(g2))  # 0=Pre, 1=HC
age = np.concatenate([cov_pre["Age"].values, cov_hc["Age"].values])
age_c = age - np.mean(age)
head_motion = np.concatenate([cov_pre["HeadMotion"].values, cov_hc["HeadMotion"].values])
head_motion_c = head_motion - np.mean(head_motion)

# Sex 兼容数字编码（0/1）或中文男女编码
def _sex_to_num(series):
    mapper = {"男": 1, "女": 0, "male": 1, "female": 0, "m": 1, "f": 0}
    if series.dtype.kind in {"i", "u", "f"}:  # already numeric
        return series.astype(float)
    return series.str.lower().map(mapper).astype(float)

sex = np.concatenate([
    _sex_to_num(cov_pre["Sex"]),
    _sex_to_num(cov_hc["Sex"])
])

# ======================================================
# 判断指标类型
# ======================================================
is_global = False
is_nodal = False
if g1.ndim == 1 or (g1.ndim==2 and g1.shape[1]==1):
    is_global = True
elif g1.ndim==2 and g1.shape[1]>1:
    is_nodal = True
    n_nodes = g1.shape[1]
else:
    raise ValueError("无法识别的数据维度")

# ======================================================
# 定义 ANCOVA + 效应量分析函数
# ======================================================
def ancova_effect(y, group, age, sex, head_motion):
    df = pd.DataFrame({
        "y": y,
        "Group": group,
        "Age": age,
        "Sex": sex,
        "HeadMotion": head_motion
    })
    model = smf.ols("y ~ Group + Age + Sex + HeadMotion", data=df).fit()
    
    # 获取 Group t/p
    t_val = model.tvalues["Group"]
    p_val = model.pvalues["Group"]

    # Group 专属 F 与 partial eta^2
    anova_table = sm.stats.anova_lm(model, typ=2)
    f_val = anova_table.loc["Group", "F"]
    df_effect = anova_table.loc["Group", "df"]
    df_error = model.df_resid
    partial_eta2 = (f_val * df_effect) / (f_val * df_effect + df_error) if pd.notnull(f_val) else np.nan

    # 组的调整均值
    mean_pre_adj = model.predict(df.assign(Group=0)).mean()
    mean_hc_adj = model.predict(df.assign(Group=1)).mean()

    # Cohen's d (基于 MSE)
    sd_resid = np.sqrt(model.mse_resid)
    cohen_d = (mean_hc_adj - mean_pre_adj) / sd_resid if sd_resid > 0 else np.nan

    return t_val, p_val, partial_eta2, cohen_d, mean_pre_adj, mean_hc_adj

# ======================================================
# ① 全局指标分析
# ======================================================
if is_global:
    t_val, p_val, eta2, d_val, mean_pre_adj, mean_hc_adj = ancova_effect(y_values, group_values, age_c, sex, head_motion_c)
    df_global = pd.DataFrame({
        "Metric":[metric_name],
        "Mean_Pre_adj":[mean_pre_adj],
        "Mean_HC_adj":[mean_hc_adj],
        "t_value":[t_val],
        "p_value":[p_val],
        "partial_eta2":[eta2],
        "Cohens_d":[d_val],
        "Significant_p<0.05":[p_val<0.05]
    })
    df_global.to_csv(os.path.join(OUT_DIR,f"{metric_name}_GlobalStats.csv"),index=False)
    print("全局指标 ANCOVA 结果：")
    print(df_global.round(4))

    # 可视化
    plt.figure(figsize=(5,4))
    means = [mean_pre_adj, mean_hc_adj]
    plt.bar(["Pre","HC"], means, color=["steelblue","darkorange"], alpha=0.8)
    sig_label = f"p={p_val:.4f}" + (" *" if p_val<0.05 else "")
    plt.text(0.5, max(means)*1.05, sig_label, ha='center')
    plt.ylabel(metric_name)
    plt.title("Global metric (ANCOVA adjusted)")
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR,f"{metric_name}_Global_Bar.png"),dpi=300)
    plt.show()

# ======================================================
# ② 节点指标分析
# ======================================================
if is_nodal:
    node_names = pd.read_csv(NODE_NAME_FILE, header=None)[0].tolist()
    network_labels = np.loadtxt(NETWORK_LABEL_FILE, dtype=int)
    NETWORK_NAME_MAP = {4:"SalVAN",6:"FPN",7:"DMN"}
    network_names = [NETWORK_NAME_MAP.get(l,"Other") for l in network_labels]

    assert len(node_names) == n_nodes, "节点名称数量与指标列数不一致"
    assert len(network_labels) == n_nodes, "网络标签数量与指标列数不一致"

    t_vals = np.zeros(n_nodes)
    p_vals = np.zeros(n_nodes)
    eta2_vals = np.zeros(n_nodes)
    d_vals = np.zeros(n_nodes)
    mean_pre_adj = np.zeros(n_nodes)
    mean_hc_adj = np.zeros(n_nodes)

    for i in range(n_nodes):
        y_node = np.concatenate([g1[:,i], g2[:,i]])
        t_vals[i], p_vals[i], eta2_vals[i], d_vals[i], mean_pre_adj[i], mean_hc_adj[i] = ancova_effect(
            y_node, group_values, age_c, sex, head_motion_c)

    # FDR 校正
    reject_fdr, p_fdr = fdrcorrection(p_vals, alpha=0.05)

    df_nodal = pd.DataFrame({
        "Node_ID": np.arange(1,n_nodes+1),
        "Node_Name": node_names,
        "Network": network_names,
        "t_value": t_vals,
        "p_uncorrected": p_vals,
        "p_FDR": p_fdr,
        "partial_eta2": eta2_vals,
        "Cohens_d": d_vals,
        "Mean_Pre_adj": mean_pre_adj,
        "Mean_HC_adj": mean_hc_adj,
        "Sig_uncorrected_p<0.05": p_vals<0.05,
        "Sig_FDR_p<0.05": reject_fdr
    })

    df_nodal.to_csv(os.path.join(OUT_DIR,f"{metric_name}_NodalStats_All.csv"),index=False)
    df_nodal[df_nodal["Sig_uncorrected_p<0.05"]].to_csv(os.path.join(OUT_DIR,f"{metric_name}_Nodal_Sig_uncorrected.csv"),index=False)
    df_nodal[df_nodal["Sig_FDR_p<0.05"]].to_csv(os.path.join(OUT_DIR,f"{metric_name}_Nodal_Sig_FDR.csv"),index=False)

    print("节点指标 ANCOVA 结果：")
    print(df_nodal.head(10).round(4))

    # t-value 热图
    plt.figure(figsize=(12,3))
    sns.heatmap(t_vals[np.newaxis,:], cmap="coolwarm", center=0, cbar_kws={"label":"t value (ANCOVA adjusted)"})
    plt.xlabel(f"Node (n={n_nodes})")
    plt.yticks([])
    plt.title(f"Nodal t-values (ANCOVA adjusted): {metric_name}")
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR,f"{metric_name}_Nodal_tMap.png"),dpi=300)
    plt.show()

print(f"\n所有结果已保存至：{OUT_DIR}")
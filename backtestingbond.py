import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
import pandas_datareader.data as web
from scipy.interpolate import PchipInterpolator



CURVE_PATH = Path("treasury_curve.parquet")
EVENT_PATH = Path("event_calendar.csv")   # CPI/FOMC/

TENORS_FLY = (2.0, 5.0, 10.0)            # 2s5s10s fly: belly=5y, wings=2y&10y
H_MONTH = 1                              # roll-down horizon = 1M
H = H_MONTH / 12.0

ANNUAL_DAYS = 252
TARGET_DV01 = 100000                     #  |DV01| ($/bp)
CAPITAL_BASE = TARGET_DV01 * 100

MAX_HOLD_DAYS = 63                       # max holding period ~ 3M

COST_BP_PER_SIDE = 0.15                  # cost of one leg



def build_or_load_curve(curve_path: Path) -> pd.DataFrame:
    if curve_path.exists():
        print(f"Loading curve from {curve_path}")
        return pd.read_parquet(curve_path).sort_index()

    print("treasury_curve.parquet not there，download from fred...")

    series = ["DGS3MO","DGS6MO","DGS1","DGS2","DGS3",
              "DGS5","DGS7","DGS10","DGS20","DGS30"]
    tenor_map = {
        "DGS3MO":0.25,"DGS6MO":0.5,"DGS1":1.0,"DGS2":2.0,"DGS3":3.0,
        "DGS5":5.0,"DGS7":7.0,"DGS10":10.0,"DGS20":20.0,"DGS30":30.0,
    }

    raw = web.DataReader(series, "fred", start="2015-01-01")
    yc = raw.dropna(how="all").ffill() / 100.0
    yc = yc.rename(columns=tenor_map)
    yc = yc[sorted(yc.columns, key=float)]
    yc.index.name = "date"

    yc.to_parquet(curve_path)
    print(f"Saved curve to {curve_path}")
    return yc

curve_df = build_or_load_curve(CURVE_PATH)

# =========================
# Event calendar & blocker
# =========================

def load_event_calendar(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    return df.sort_values("date")

event_df = load_event_calendar(EVENT_PATH)

def build_event_blocker(idx: pd.DatetimeIndex,
                        event_df: pd.DataFrame,
                        window_days: int = 2) -> pd.Series:
    """
   event filter。
    """
    blocked = pd.Series(False, index=idx)
    ev_dates = event_df["date"].dt.normalize().unique()
    for d in ev_dates:
        start = d - pd.Timedelta(days=window_days)
        end = d + pd.Timedelta(days=window_days)
        mask = (blocked.index >= start) & (blocked.index <= end)
        blocked.loc[mask] = True
    return blocked

event_block = build_event_blocker(curve_df.index, event_df, window_days=2)

# =========================
# 基础函数
# =========================

def zero_coupon(y: float, T: float):
    """
    Zero-coupon :
      P = exp(-yT)
      DV01 = P * T * 1bp
    """
    P = np.exp(-y * T)
    dv01 = P * T * 1e-4
    return P, dv01

def interp_yield(curve_row: pd.Series, T: float) -> float:
    xs = curve_row.index.values.astype(float)
    ys = curve_row.values.astype(float)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]

    if len(xs) < 2:
        return float(np.interp(T, xs, ys))
    try:
        pchip = PchipInterpolator(xs, ys)
        if T < xs[0] or T > xs[-1]:
            return float(np.interp(T, xs, ys))
        return float(pchip(T))
    except Exception:
        return float(np.interp(T, xs, ys))

def roll_down_dy(curve_row: pd.Series, T: float, H: float) -> float:

    if T - H <= 0:
        return 0.0
    y_T  = interp_yield(curve_row, T)
    y_TH = interp_yield(curve_row, T - H)
    return y_TH - y_T

# =========================
# one leg CRD
# =========================

def crd_single_leg(curve_row: pd.Series, T: float, H: float, fund_rate: float):

    y = interp_yield(curve_row, T)
    P, dv01 = zero_coupon(y, T)

    dy_roll = roll_down_dy(curve_row, T, H)  # decimal
    dy_bps = dy_roll * 1e4                   # to bp

    roll_pnl = -dv01 * dy_bps                # $ per 1 notional
    carry = (y - fund_rate) * H * P          # $ per 1 notional

    crd_ret = (roll_pnl + carry) / P
    return crd_ret, P, dv01

# =========================
# DV01-neutral fly weight
# =========================

def fly_weight_DV01_neutral(dv01_L, dv01_B, dv01_R):
    """

      wL*dv01_L + 1*dv01_B + wR*dv01_R = 0   (DV01 neutral)
      wL + 1 + wR = 0                        (
    """
    A = np.array([[dv01_L, dv01_R],
                  [1.0,    1.0]])
    b = np.array([-dv01_B, -1.0])
    w_L, w_R = np.linalg.solve(A, b)
    return float(w_L), 1.0, float(w_R)


# calculate CRD Signal


def compute_signal(curve_row: pd.Series, tenors, H: float):
    T_L, T_B, T_R = tenors
    fund_rate = float(curve_row.iloc[0])  # use 3M rate as funding rate

    crd_L, P_L, dv01_L = crd_single_leg(curve_row, T_L, H, fund_rate)
    crd_B, P_B, dv01_B = crd_single_leg(curve_row, T_B, H, fund_rate)
    crd_R, P_R, dv01_R = crd_single_leg(curve_row, T_R, H, fund_rate)

    wL, wB, wR = fly_weight_DV01_neutral(dv01_L, dv01_B, dv01_R)

    #
    fly_crd_H = (
        wL * P_L * crd_L +
        wB * P_B * crd_B +
        wR * P_R * crd_R
    )

    # annualized
    fly_crd_annual = fly_crd_H / H

    # absolute DV01
    fly_dv01_abs = (
        abs(wL * dv01_L) +
        abs(wB * dv01_B) +
        abs(wR * dv01_R)
    )

    # bp/year per $1 DV01
    sig_bp = (fly_crd_annual / max(fly_dv01_abs, 1e-12)) * 1e4

    return float(sig_bp), wL, wB, wR, float(fly_dv01_abs)

# ---- signal ----
sig_rows = []
for date, row in curve_df.iterrows():
    sig_bp, wL, wB, wR, fly_dv01 = compute_signal(row, TENORS_FLY, H)
    sig_rows.append({
        "date": date,
        "sig_raw_bp": sig_bp,
        "wL": wL,
        "wB": wB,
        "wR": wR,
        "fly_dv01": fly_dv01,
    })

signal_df = pd.DataFrame(sig_rows).set_index("date")


signal_df["sig_bp"] = signal_df["sig_raw_bp"].rolling(5, min_periods=1).mean()

# Regime filter: only open when 2 and 10s is stable
if 2.0 in curve_df.columns and 10.0 in curve_df.columns:
    slope_2_10 = curve_df[10.0] - curve_df[2.0]
    slope_chg_20d = slope_2_10.diff().abs().rolling(20).mean()
    regime_ok = slope_chg_20d < 0.0005  # 20日平均变动 < 5bp
else:
    regime_ok = pd.Series(True, index=curve_df.index)


stable_sig = signal_df.loc[regime_ok, "sig_bp"].dropna()
abs_sig = stable_sig.abs()
ENTRY_SIG = abs_sig.quantile(0.85)   # top 15% open position
EXIT_SIG  = abs_sig.quantile(0.50)   # back to middle close

print("=== Signal summary (2s5s10s, 1M, stable regime) ===")
print(stable_sig.describe())
print("ENTRY_SIG (bp/yr per $DV01):", ENTRY_SIG)
print("EXIT_SIG  (bp/yr per $DV01):", EXIT_SIG)
print("Active regime days:", regime_ok.sum(), "/", len(regime_ok))


# backtesting


def zero_price_from_curve(curve_row: pd.Series, T: float):
    y = interp_yield(curve_row, T)
    P, _ = zero_coupon(y, T)
    return P

dates = signal_df.index

prev_curve = None
prev_pos_dir = 0        # +1 long fly, -1 short fly, 0 flat
prev_wL = prev_wB = prev_wR = 0.0
prev_scale = 0.0
prev_hold_days = 0

bt_rows = []

for date in dates:
    curve_today = curve_df.loc[date]
    sig = signal_df.loc[date, "sig_bp"]
    wL_sig = signal_df.loc[date, "wL"]
    wB_sig = signal_df.loc[date, "wB"]
    wR_sig = signal_df.loc[date, "wR"]
    fly_dv01_sig = signal_df.loc[date, "fly_dv01"]

    is_regime_ok = bool(regime_ok.get(date, True))
    is_event_block = bool(event_block.get(date, False))

    # PnL
    daily_pnl = 0.0
    if prev_curve is not None and prev_pos_dir != 0 and prev_scale > 0:
        T_L, T_B, T_R = TENORS_FLY


        P_L0 = zero_price_from_curve(prev_curve, T_L)
        P_B0 = zero_price_from_curve(prev_curve, T_B)
        P_R0 = zero_price_from_curve(prev_curve, T_R)


        P_L1 = zero_price_from_curve(curve_today, T_L)
        P_B1 = zero_price_from_curve(curve_today, T_B)
        P_R1 = zero_price_from_curve(curve_today, T_R)

        fly_leg_pnl = (
            prev_wL * (P_L1 - P_L0) +
            prev_wB * (P_B1 - P_B0) +
            prev_wR * (P_R1 - P_R0)
        )

        # carry
        dt = 1.0 / ANNUAL_DAYS
        fund_prev = float(prev_curve.iloc[0])
        y_L_prev = interp_yield(prev_curve, T_L)
        y_B_prev = interp_yield(prev_curve, T_B)
        y_R_prev = interp_yield(prev_curve, T_R)

        carry_L = (y_L_prev - fund_prev) * dt * P_L0
        carry_B = (y_B_prev - fund_prev) * dt * P_B0
        carry_R = (y_R_prev - fund_prev) * dt * P_R0

        fly_carry = (
            prev_wL * carry_L +
            prev_wB * carry_B +
            prev_wR * carry_R
        )

        daily_pnl = (fly_leg_pnl + fly_carry) * prev_scale * prev_pos_dir

    #  open position
    new_pos_dir = prev_pos_dir
    new_wL, new_wB, new_wR = prev_wL, prev_wB, prev_wR
    new_scale = prev_scale
    new_hold_days = prev_hold_days


    if is_event_block and prev_pos_dir != 0:
        new_pos_dir = 0
        new_scale = 0.0
        new_hold_days = 0
    elif prev_pos_dir == 0:
        if (not is_event_block) and is_regime_ok:
            if sig > ENTRY_SIG:
                # long fly: long belly / short wings
                new_pos_dir = 1
                new_wL, new_wB, new_wR = wL_sig, wB_sig, wR_sig
                new_scale = TARGET_DV01 / max(fly_dv01_sig, 1e-12)
                new_hold_days = 0
            elif sig < -ENTRY_SIG:
                # short fly
                new_pos_dir = -1
                new_wL, new_wB, new_wR = wL_sig, wB_sig, wR_sig
                new_scale = TARGET_DV01 / max(fly_dv01_sig, 1e-12)
                new_hold_days = 0
    else:
        # close position
        new_hold_days = prev_hold_days + 1
        exit_cond = (abs(sig) < EXIT_SIG) or (new_hold_days >= MAX_HOLD_DAYS)
        if exit_cond:
            new_pos_dir = 0
            new_scale = 0.0
            new_hold_days = 0

    # cost
    trade_cost = 0.0
    if new_pos_dir != prev_pos_dir:
        step_units = 1 if (prev_pos_dir == 0 or new_pos_dir == 0) else 2
        trade_cost = step_units * COST_BP_PER_SIDE * 1e-4 * TARGET_DV01
        daily_pnl -= trade_cost

    bt_rows.append({
        "date": date,
        "sig_bp": sig,
        "pos_dir": prev_pos_dir,      #build position
        "daily_pnl": daily_pnl,
        "hold_days": prev_hold_days,
        "regime_ok": is_regime_ok,
        "event_block": is_event_block,
        "trade_cost": trade_cost,
    })

    prev_curve = curve_today
    prev_pos_dir = new_pos_dir
    prev_wL, prev_wB, prev_wR = new_wL, new_wB, new_wR
    prev_scale = new_scale
    prev_hold_days = new_hold_days

bt = pd.DataFrame(bt_rows).set_index("date")


# result

bt["cum_pnl"] = bt["daily_pnl"].cumsum()
bt["ret"] = bt["daily_pnl"] / CAPITAL_BASE
bt["cum_ret"] = (1 + bt["ret"].fillna(0)).cumprod() - 1

valid = bt["ret"].replace([np.inf, -np.inf], np.nan).dropna()
if len(valid) > 1:
    ann_ret = valid.mean() * ANNUAL_DAYS
    ann_vol = valid.std(ddof=1) * np.sqrt(ANNUAL_DAYS)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else np.nan
else:
    ann_ret = ann_vol = sharpe = np.nan

print("\n=== Curve-space 2s5s10s Fly CRD Backtest "
      "(regime + events + costs, 1M horizon, tail-only) ===")
print(f"Ann.Return: {ann_ret:.4%}")
print(f"Ann.Vol:    {ann_vol:.4%}")
print(f"Sharpe:     {sharpe:.2f}")
print(f"Total PnL:  {bt['daily_pnl'].sum():.2f}")
print(f"Non-zero position days: {(bt['pos_dir'] != 0).sum()}")
print(f"Total trade cost: {bt['trade_cost'].sum():.2f}")


# graph


plt.figure(figsize=(10, 4))
signal_df["sig_bp"].plot()
plt.axhline(0, color="gray", linestyle="--", alpha=0.7)
plt.title("2s5s10s Fly CRD Signal (1M horizon, 5D MA)")
plt.ylabel("Signal (bp/year per $1 DV01)")
plt.grid(True, linestyle="--", alpha=0.4)
plt.tight_layout()
plt.show()

plt.figure(figsize=(10, 4))
bt["cum_pnl"].plot()
plt.title("2s5s10s Fly Cumulative PnL (Filtered & Costed)")
plt.ylabel("Cumulative PnL ($)")
plt.grid(True, linestyle="--", alpha=0.4)
plt.tight_layout()
plt.show()

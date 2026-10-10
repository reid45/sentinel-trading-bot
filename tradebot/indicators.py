"""Pure-python indicators. All return lists aligned with input; early values are None."""
def ema(x, n):
    k = 2.0 / (n + 1); out = [None] * len(x); e = None
    for i, v in enumerate(x):
        if v is None: continue
        e = v if e is None else e + k * (v - e)
        if i >= n - 1: out[i] = e
    return out

def sma(x, n):
    out = [None] * len(x); s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n: s -= x[i - n]
        if i >= n - 1: out[i] = s / n
    return out

def rsi(c, n):
    out = [None] * len(c); ag = al = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; g, l = max(d, 0), max(-d, 0)
        if i <= n:
            ag += g / n; al += l / n
        else:
            ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n
        if i >= n: out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out

def atr(h, l, c, n):
    out = [None] * len(c); a = None
    for i in range(1, len(c)):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i < n: a = (a or 0) + tr / n
        elif i == n: a = (a or 0) + tr / n; out[i] = a
        else: a = (a * (n - 1) + tr) / n; out[i] = a
    return out

def highest(x, n):  # max of the previous n bars (excludes current)
    out = [None] * len(x)
    for i in range(n, len(x)): out[i] = max(x[i - n:i])
    return out

def lowest(x, n):
    out = [None] * len(x)
    for i in range(n, len(x)): out[i] = min(x[i - n:i])
    return out

def adx(h, l, c, n):
    out = [None] * len(c); tr_s = pd_s = md_s = 0.0; dx = []
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm = up if up > dn and up > 0 else 0.0; mdm = dn if dn > up and dn > 0 else 0.0
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i <= n: tr_s += tr; pd_s += pdm; md_s += mdm
        else: tr_s = tr_s - tr_s / n + tr; pd_s = pd_s - pd_s / n + pdm; md_s = md_s - md_s / n + mdm
        if i >= n and tr_s > 0:
            pdi, mdi = 100 * pd_s / tr_s, 100 * md_s / tr_s
            dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
            if len(dx) == n: out[i] = sum(dx) / n
            elif len(dx) > n: out[i] = (out[i - 1] * (n - 1) + dx[-1]) / n
    return out

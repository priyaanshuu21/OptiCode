# f(int a, int b) -> int
#   params: a, b
    i1: t1 = a / b  [may trap]
    i2: c = t1
    i3: t2 = a % b  [may trap]
    i4: d = t2
    i5: t3 = c + d
    i6: return t3
    i7: return 0  [synthetic]

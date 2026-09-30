# f(int n) -> int
#   params: n
    i1: s = 0
    i2: i = 0
    i3: Lstart1:
    i4: t1 = i < n
    i5: if_false t1 goto Lend2
    i6: t2 = s + i
    i7: s = t2
    i8: t3 = i + 1
    i9: i = t3
    i10: goto Lstart1
    i11: Lend2:
    i12: return s
    i13: return 0  [synthetic]

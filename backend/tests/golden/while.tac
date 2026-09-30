# f(int n) -> int
#   params: n
    i1: i = 0
    i2: Lstart1:
    i3: t1 = i < n
    i4: if_false t1 goto Lend2
    i5: t2 = i + 1
    i6: i = t2
    i7: goto Lstart1
    i8: Lend2:
    i9: return i
    i10: return 0  [synthetic]

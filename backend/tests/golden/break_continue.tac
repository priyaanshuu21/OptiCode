# f(int n) -> int
#   params: n
    i1: s = 0
    i2: i = 0
    i3: Lstart1:
    i4: t1 = i < n
    i5: if_false t1 goto Lend2
    i6: t2 = i == 1
    i7: if_false t2 goto Lskip4
    i8: goto Lstart1
    i9: Lskip4:
    i10: t3 = i == 7
    i11: if_false t3 goto Lskip6
    i12: goto Lend2
    i13: Lskip6:
    i14: t4 = s + i
    i15: s = t4
    i16: t5 = i + 1
    i17: i = t5
    i18: goto Lstart1
    i19: Lend2:
    i20: return s
    i21: return 0  [synthetic]

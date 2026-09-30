# f(int n) -> int
#   params: n
    i1: s = 0
    i2: i = 0
    i3: Lstart1:
    i4: t1 = i < n
    i5: if_false t1 goto Lend2
    i6: t2 = i == 1
    i7: if_false t2 goto Lskip5
    i8: goto Lcont3
    i9: Lskip5:
    i10: t3 = i == 7
    i11: if_false t3 goto Lskip7
    i12: goto Lend2
    i13: Lskip7:
    i14: t4 = s + i
    i15: s = t4
    i16: Lcont3:
    i17: t5 = i + 1
    i18: i = t5
    i19: goto Lstart1
    i20: Lend2:
    i21: return s
    i22: return 0  [synthetic]

# f(int a, int b, int c) -> int
#   params: a, b, c
    i1: if_false a goto Lshort2
    i2: if_false b goto Lright4
    i3: goto Ltrue5
    i4: Lright4:
    i5: if_false c goto Lfalse6
    i6: Ltrue5:
    i7: t2 = 1
    i8: goto Lend3
    i9: Lfalse6:
    i10: t2 = 0
    i11: Lend3:
    i12: if_false t2 goto Lshort2
    i13: t1 = 1
    i14: goto Lend1
    i15: Lshort2:
    i16: t1 = 0
    i17: Lend1:
    i18: return t1
    i19: return 0  [synthetic]

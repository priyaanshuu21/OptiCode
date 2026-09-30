# f(int a, int b) -> int
#   params: a, b
    i1: if_false a goto Lright2
    i2: goto Ltrue3
    i3: Lright2:
    i4: if_false b goto Lfalse4
    i5: Ltrue3:
    i6: t1 = 1
    i7: goto Lend1
    i8: Lfalse4:
    i9: t1 = 0
    i10: Lend1:
    i11: return t1
    i12: return 0  [synthetic]

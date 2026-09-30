# f(int a, int b) -> int
#   params: a, b
    i1: if_false a goto Lshort2
    i2: if_false b goto Lshort2
    i3: t1 = 1
    i4: goto Lend1
    i5: Lshort2:
    i6: t1 = 0
    i7: Lend1:
    i8: return t1
    i9: return 0  [synthetic]

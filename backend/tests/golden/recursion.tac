# fact(int n) -> int
#   params: n
    i1: t1 = n <= 1
    i2: if_false t1 goto Lskip2
    i3: return 1
    i4: Lskip2:
    i5: t2 = n - 1
    i6: t3 = fact(t2)
    i7: t4 = n * t3
    i8: return t4
    i9: return 0  [synthetic]

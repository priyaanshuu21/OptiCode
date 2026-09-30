# f(int a) -> int
#   params: a
    i1: if_false a goto Lelse2
    i2: return 1
    i3: goto Lcont1
    i4: Lelse2:
    i5: return 2
    i6: Lcont1:
    i7: return 0  [synthetic]

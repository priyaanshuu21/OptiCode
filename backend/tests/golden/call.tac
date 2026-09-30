# add(int a, int b) -> int
#   params: a, b
    i1: t1 = a + b
    i2: return t1
    i3: return 0  [synthetic]

# f(int a) -> int
#   params: a_2
    i1: t1 = add(a_2, 2)
    i2: return t1
    i3: return 0  [synthetic]

from immutables import Map

def f(d):
    with d.mutate() as mm:
        mm.set(42, 1)

    d = mm.finish()
    # x = dict(d)
    # x[42] = 1
    # dict.

ddd = Map({42: 8})

f(ddd)

print(ddd)

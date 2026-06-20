#include "mathlib.h"
namespace mathlib {
int Adder::add(int a, int b) { return a + b; }
int Adder::add_twice(int a, int b) { return add(a, b) + add(a, b); }
int square(int x) { Adder a; return a.add(x, x); }
}

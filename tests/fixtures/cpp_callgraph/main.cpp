#include "mathlib.h"
int compute(int n) {
  mathlib::Adder a;
  return a.add_twice(n, 1) + mathlib::square(n);
}

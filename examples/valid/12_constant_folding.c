/* Constant arithmetic: expressions the compiler can evaluate without running anything.
 *
 * Every expression below is built entirely from literals, so folding it at compile time
 * is safe. Keeping both the folded and unfolded forms in one file makes the difference
 * easy to inspect.
 */

int folded_add() {
    return 2 + 3 * 4;
}

int folded_subtract() {
    return 100 - 7 - 3;
}

int folded_divide() {
    return 84 / 2;
}

int folded_modulo() {
    return 17 % 5;
}

int folded_compare() {
    return 3 * 7 > 20;
}

int folded_logic() {
    return 1 && 0;
}

float folded_float() {
    return 1.5 * 4.0;
}

float folded_float_mix() {
    return 1.5 + 0.25 * 2.0;
}

int mixed_with_variable(int x) {
    /* Not foldable: x is only known at run time. */
    return 10 + x * 2;
}

int main() {
    printf("2 + 3 * 4 = %d\n", folded_add());
    printf("100 - 7 - 3 = %d\n", folded_subtract());
    printf("84 / 2 = %d\n", folded_divide());
    printf("17 %% 5 = %d\n", folded_modulo());
    printf("3 * 7 > 20 = %d\n", folded_compare());
    printf("1 && 0 = %d\n", folded_logic());
    printf("1.5 * 4.0 = %f\n", folded_float());
    printf("1.5 + 0.25 * 2.0 = %f\n", folded_float_mix());
    printf("10 + x * 2 with x=5: %d\n", mixed_with_variable(5));
    return 0;
}
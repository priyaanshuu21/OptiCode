/* Reading input with scanf and printing it back with printf. */

int main() {
    int count = 0;
    float price = 0.0;

    printf("How many items? ");
    scanf("%d", &count);

    printf("Price each? ");
    scanf("%f", &price);

    float total = price * count;
    printf("Total for %d items at %f each: %f\n", count, price, total);

    /* Several values in one format string, each with its own specifier. */
    int a;
    int b;
    printf("Enter two integers: ");
    scanf("%d %d", &a, &b);
    printf("sum=%d diff=%d product=%d\n", a + b, a - b, a * b);

    /* %% prints a literal percent sign and consumes no argument. */
    printf("100%% of the total is %f\n", total);

    return 0;
}
/* Arithmetic with both numeric types, including the implicit int -> float promotion. */

float average(int total, int count) {
    return total / count;
}

int main() {
    int a = 17;
    int b = 5;
    int sum = a + b;
    int difference = a - b;
    int product = a * b;
    int quotient = a / b;
    int remainder = a % b;

    printf("sum=%d\n", sum);
    printf("difference=%d\n", difference);
    printf("product=%d\n", product);
    printf("quotient=%d remainder=%d\n", quotient, remainder);

    float total = a * b;
    float mean = average(sum, b);
    printf("total=%f mean=%f\n", total, mean);

    return 0;
}
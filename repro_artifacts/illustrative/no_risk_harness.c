#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"

static void test_parse(const char *input) {
    cJSON *json = cJSON_Parse(input);
    if (json) {
        printf("Parsed successfully\n");
        char *printed = cJSON_Print(json);
        if (printed) {
            printf("Result: %s\n", printed);
            free(printed);
        }
        cJSON_Delete(json);
    } else {
        printf("Parse failed: %s\n", cJSON_GetErrorPtr());
    }
}

int main(void) {
    const char *valid_json = "{\"name\":\"test\",\"value\":42}";
    const char *invalid_json = "{broken}";

    printf("Testing valid JSON:\n");
    test_parse(valid_json);

    printf("\nTesting invalid JSON:\n");
    test_parse(invalid_json);

    return 0;
}

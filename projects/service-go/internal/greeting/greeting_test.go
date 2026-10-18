package greeting

import (
	"errors"
	"testing"
)

func TestHello(t *testing.T) {
	cases := []struct {
		name    string
		in      string
		want    string
		wantErr error
	}{
		{"plain", "Gopher", "Hello, Gopher!", nil},
		{"trims spaces", "  Ana ", "Hello, Ana!", nil},
		{"empty", "   ", "", ErrEmptyName},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Hello(tc.in)
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("err = %v, want %v", err, tc.wantErr)
			}
			if got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}
